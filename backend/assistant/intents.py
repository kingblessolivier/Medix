"""The question grammar.

Deliberately not a language model. A pharmacist asking "which batches
expire in 60 days" needs the right rows, not a plausible sentence, and a
system that answers plausibly when it does not know is worse here than
one that says it does not know — the answer feeds a decision about
medicines.

So the grammar is a small, explicit list: patterns map to service calls
that already exist and are already tested. Anything unmatched says so and
offers search. Nothing is invented, nothing is summarised, and every row
is the same row the corresponding screen would show.

Three things are refused outright rather than answered badly:

* **clinical questions** — no symptom-to-drug mapping, ever, at any
  confidence. See CLAUDE.md and docs/12-compliance.md.
* **"net profit"** — the figure the system can compute is an estimated
  operating result, and calling it profit would be a claim about tax and
  overheads it has no basis for.
* **anything that moves stock, money or a regulated record** — those come
  back as a proposal for a person to confirm, never as a completed act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from django.utils import timezone

from core.models import Organization, User


@dataclass(frozen=True)
class Answer:
    """What came back, and where to go and act on it."""

    intent: str
    #: One line, stating the finding. Twelve words, per docs/23.
    headline: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    #: The screen that can act on this. An answer with no destination is
    #: a fact rather than a next step.
    screen: str = ""
    #: A suggested action awaiting confirmation. Never performed here.
    proposal: dict | None = None
    #: Where a figure needs qualifying — an estimate, a partial period.
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "headline": self.headline,
            "columns": self.columns,
            "rows": self.rows,
            "screen": self.screen,
            "proposal": self.proposal,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

#: Symptom and advice language. Matched before anything else, because a
#: clinical question that falls through to a product search comes back
#: looking exactly like an answer.
CLINICAL = re.compile(
    r"\b(what should i (give|dispense|recommend|take)|treat(ment)? for|"
    r"(good|best) for (a )?\w+|dose( for|age)|how much should|"
    r"cure|symptom|headache|fever|malaria|diarrh|infection|pregnan|"
    r"is it safe (to|for)|can (i|a patient) (take|use))\b",
    re.I,
)

#: Not a refusal — a redirection. The number exists; its name does not.
NET_PROFIT = re.compile(r"\bnet profit\b|\bprofit after\b|\bbottom line\b", re.I)


def _clinical(**_) -> Answer:
    return Answer(
        intent="clinical",
        headline="No clinical advice",
        note=(
            "Medix reports stock, price and registration. It does not "
            "recommend a medicine for a condition. Product information and "
            "the approved leaflet are on the product record."
        ),
        screen="catalogue",
    )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _days(question: str, fallback: int) -> int:
    """A number of days named in the question, else the configured one."""
    match = re.search(r"(\d+)\s*(day|week|month)", question, re.I)
    if not match:
        return fallback
    value, unit = int(match.group(1)), match.group(2).lower()
    return value * {"day": 1, "week": 7, "month": 30}[unit]


def _period(question: str, *, fallback_days: int = 30) -> tuple[date, date]:
    """The window a question is about, as whole days.

    Defaults to the last thirty rather than this month: "what sold" on
    the second of the month should not answer with two days of trading.
    """
    today = timezone.localdate()
    if re.search(r"\bthis month\b", question, re.I):
        return today.replace(day=1), today
    if re.search(r"\blast month\b", question, re.I):
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if re.search(r"\b(this year|ytd)\b", question, re.I):
        return today.replace(month=1, day=1), today
    return today - timedelta(days=_days(question, fallback_days)), today


def _money(value: int, currency: str = "RWF") -> str:
    from core.money import Money

    return str(Money(value, currency))


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _expiring(*, organization: Organization, question: str, user: User) -> Answer:
    from inventory import checks

    # The rule's own horizon is the default; a question that names one
    # narrows within it rather than widening past a configured policy.
    horizon = _days(question, 90)
    rows = [
        {
            "batch": alert.detail,
            "product": alert.title.split(" expires in ")[0],
            "days left": str(alert.meta["days"]),
            "on hand": f"{alert.meta.get('quantity_base', 0):,}",
        }
        for alert in checks.short_dated_batches(organization=organization)
        if alert.meta["days"] <= horizon
    ]
    return Answer(
        intent="expiring",
        headline=f"{len(rows)} batches expire within {horizon} days",
        columns=["batch", "product", "days left", "on hand"],
        rows=rows,
        screen="inventory",
    )


def _low_stock(*, organization: Organization, question: str, user: User) -> Answer:
    from inventory import checks

    alerts = checks.below_reorder_point(organization=organization)
    rows = [
        {
            "product": alert.title.replace(" below reorder point", ""),
            "on hand": f"{alert.meta.get('on_hand', 0):,}",
            "reorder at": f"{alert.meta.get('reorder_point', 0):,}",
        }
        for alert in alerts
    ]

    proposal = None
    if rows:
        # A draft order is still a record someone has to release, but the
        # Assistant does not get to create it on its own say-so.
        proposal = {
            "action": "draft_order",
            "arguments": {"products": [a.subject_id for a in alerts]},
            "effect": f"Creates a draft order for {len(rows)} products. Nothing is sent.",
        }

    return Answer(
        intent="low_stock",
        headline=f"{len(rows)} products below reorder point",
        columns=["product", "on hand", "reorder at"],
        rows=rows,
        screen="inventory",
        proposal=proposal,
    )


def _unpaid_invoices(*, organization: Organization, question: str, user: User) -> Answer:
    from commerce.models import Invoice, InvoiceStatus

    today = timezone.localdate()
    rows = []
    invoices = (
        Invoice.objects.filter(
            organization=organization,
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID],
        )
        .select_related("customer")
        .order_by("due_on")[:50]
    )
    overdue = 0
    for invoice in invoices:
        late = invoice.days_overdue(as_of=today)
        overdue += 1 if late else 0
        rows.append(
            {
                "invoice": invoice.number,
                "customer": invoice.customer.name,
                "outstanding": _money(invoice.outstanding, invoice.currency),
                "due": invoice.due_on.isoformat() if invoice.due_on else "",
                "overdue days": str(late) if late else "",
            }
        )

    return Answer(
        intent="unpaid_invoices",
        headline=f"{len(rows)} invoices unpaid, {overdue} overdue",
        columns=["invoice", "customer", "outstanding", "due", "overdue days"],
        rows=rows,
        screen="finance",
    )


def _open_orders(*, organization: Organization, question: str, user: User) -> Answer:
    from commerce.models import PurchaseOrder, PurchaseOrderStatus

    waiting = [
        PurchaseOrderStatus.PENDING_APPROVAL,
        PurchaseOrderStatus.SUBMITTED,
        PurchaseOrderStatus.CONFIRMED,
        PurchaseOrderStatus.PREPARING,
    ]
    orders = (
        PurchaseOrder.objects.filter(organization=organization, status__in=waiting)
        .select_related("supplier")
        .order_by("created_at")[:50]
    )
    rows = [
        {
            "order": order.number or "draft",
            "supplier": order.supplier.name,
            "status": order.get_status_display(),
            "raised": order.created_at.date().isoformat(),
        }
        for order in orders
    ]
    return Answer(
        intent="open_orders",
        headline=f"{len(rows)} orders in progress",
        columns=["order", "supplier", "status", "raised"],
        rows=rows,
        screen="orders",
    )


def _slow_movers(*, organization: Organization, question: str, user: User) -> Answer:
    from finance import intelligence

    start, end = _period(question, fallback_days=90)
    found = intelligence.slow_movers(organization=organization, start=start, end=end)
    rows = [
        {
            "product": row["name"],
            "on hand": f"{row['on_hand']:,}",
            "sold": f"{row['sold']:,}",
            "capital held": _money(row["value"]),
            "cover": "never sold" if row["cover_days"] is None else f"{row['cover_days']} days",
        }
        for row in found
    ]
    return Answer(
        intent="slow_movers",
        headline=f"{len(rows)} products barely moved since {start:%d %b}",
        columns=["product", "on hand", "sold", "capital held", "cover"],
        rows=rows,
        screen="analytics",
        note="Held stock that did not sell. Capital, not loss.",
    )


def _best_sellers(*, organization: Organization, question: str, user: User) -> Answer:
    from finance import intelligence

    start, end = _period(question)
    found = intelligence.best_sellers(organization=organization, start=start, end=end)
    rows = [
        {
            "product": row["name"],
            "units": f"{row['units']:,}",
            "revenue": _money(row["revenue"]),
            "sales": str(row["sales"]),
        }
        for row in found
    ]
    return Answer(
        intent="best_sellers",
        headline=f"Top {len(rows)} products since {start:%d %b}",
        columns=["product", "units", "revenue", "sales"],
        rows=rows,
        screen="analytics",
    )


def _stock_outs(*, organization: Organization, question: str, user: User) -> Answer:
    from finance import intelligence

    start, end = _period(question)
    found = intelligence.stock_outs(organization=organization, start=start, end=end)
    rows = [
        {"product": row["name"], "sold": f"{row['sold']:,}", "on hand": "0"}
        for row in found
    ]
    return Answer(
        intent="stock_outs",
        headline=f"{len(rows)} products sold and now at zero",
        columns=["product", "sold", "on hand"],
        rows=rows,
        screen="inventory",
        note="Lost sales leave no trace in a sales figure.",
    )


def _cold_chain(*, organization: Organization, question: str, user: User) -> Answer:
    from inventory.telemetry import Excursion

    open_ones = (
        Excursion.objects.filter(organization=organization, resolved_at__isnull=True)
        .select_related("sensor__location")
        .order_by("-started_at")[:20]
    )
    rows = [
        {
            "location": excursion.sensor.location.name,
            "started": excursion.started_at.strftime("%d %b %H:%M"),
            "peak": f"{excursion.peak_celsius}°C",
            "held": f"{excursion.quarantined_base:,}",
            "state": "Open" if excursion.is_open else "Recovered",
        }
        for excursion in open_ones
    ]
    return Answer(
        intent="cold_chain",
        headline=f"{len(rows)} unresolved temperature excursions",
        columns=["location", "started", "peak", "held", "state"],
        rows=rows,
        screen="inventory",
        note="Stock stays quarantined until someone decides about it.",
    )


def _stock_of(*, organization: Organization, question: str, user: User) -> Answer:
    """How much of a named product is on hand, by location."""
    from catalog.models import Product
    from inventory.models import StockBalance, StockStatus

    # Strip the question, keep the medicine. Removing known phrases from
    # anywhere rather than cutting at the first keyword: "how much
    # amoxicillin do we have" carries the name in the middle.
    term = re.sub(
        r"\b(how (much|many)|do we have|have we got|is there|in stock|"
        r"stock (of|level)|left|remaining|on hand|of)\b",
        " ",
        question,
        flags=re.I,
    )
    term = re.sub(r"\s+", " ", term).strip(" ?.")
    if len(term) < 2:
        return Answer(
            intent="stock_of",
            headline="Name a product",
            screen="inventory",
        )

    product = (
        Product.objects.filter(organization=organization, name__icontains=term)
        .order_by("name")
        .first()
    )
    if product is None:
        return Answer(
            intent="stock_of",
            headline=f"No product matching “{term}”",
            screen="catalogue",
        )

    balances = (
        StockBalance.objects.filter(
            organization=organization, product=product, quantity_base__gt=0
        )
        .select_related("location")
        .order_by("location__name")
    )
    rows = [
        {
            "location": balance.location.name,
            "status": balance.get_status_display(),
            "base units": f"{balance.quantity_base:,}",
        }
        for balance in balances
    ]
    available = sum(
        balance.quantity_base
        for balance in balances
        if balance.status == StockStatus.AVAILABLE
    )
    return Answer(
        intent="stock_of",
        headline=f"{product.name}: {available:,} available",
        columns=["location", "status", "base units"],
        rows=rows,
        screen="inventory",
        note=f"Base unit is {product.base_uom.name.lower()}." if product.base_uom else "",
    )


def _operating_result(*, organization: Organization, question: str, user: User) -> Answer:
    """The figure people mean when they say profit, under its real name.

    Never "net profit". The system sees trade, not tax, not rent, not
    salaries — calling this profit would be a claim it cannot support,
    and someone would make a decision on it.
    """
    from finance import reports

    start, end = _period(question)
    report = reports.period_report(organization=organization, start=start, end=end)
    rows = [
        {"figure": "Revenue", "amount": _money(report.revenue, report.currency)},
        {"figure": "Cost of goods", "amount": _money(report.cogs, report.currency)},
        {"figure": "Gross profit", "amount": _money(report.gross_profit, report.currency)},
        {"figure": "Expenses", "amount": _money(report.expenses_total, report.currency)},
        {
            "figure": "Estimated operating result",
            "amount": _money(report.estimated_operating_result, report.currency),
        },
    ]
    return Answer(
        intent="operating_result",
        headline=f"Estimated operating result, {start:%d %b} to {end:%d %b}",
        columns=["figure", "amount"],
        rows=rows,
        screen="finance",
        note=(
            "Estimated operating result, not net profit: tax, rent, salaries "
            "and financing are outside what Medix records."
        ),
    )


def _search(*, organization: Organization, question: str, user: User) -> Answer:
    """The fallback. Whatever the words match, wherever it lives."""
    from core import search as search_module

    term = re.sub(r"^(find|where is|show me|show|search( for)?)\s+", "", question, flags=re.I)
    found = search_module.search(user=user, term=term.strip(" ?."))["results"]
    rows = [
        {"kind": hit["kind"], "name": hit["title"], "detail": hit["subtitle"]}
        for hit in found
    ]
    return Answer(
        intent="search",
        headline=f"{len(rows)} matches for “{term.strip(' ?.')}”",
        columns=["kind", "name", "detail"],
        rows=rows,
        screen=found[0]["screen"] if found else "overview",
    )


# --------------------------------------------------------------------------
# The grammar
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Intent:
    name: str
    pattern: re.Pattern
    handler: Callable[..., Answer]


#: Order matters: the first match wins, so the specific patterns come
#: before the general ones and search comes last.
INTENTS: list[Intent] = [
    Intent("clinical", CLINICAL, _clinical),
    Intent("operating_result", re.compile(
        r"\b(profit|margin|operating result|how (are|did) we do|earnings?)\b", re.I
    ), _operating_result),
    Intent("expiring", re.compile(
        r"\b(expir\w*|short[- ]dated|shelf life)\b", re.I
    ), _expiring),
    Intent("low_stock", re.compile(
        r"\b(reorder|low stock|running (low|out)|need(s)? ordering|below)\b", re.I
    ), _low_stock),
    Intent("stock_outs", re.compile(
        r"\b(stock ?outs?|out of stock|ran out|zero stock)\b", re.I
    ), _stock_outs),
    Intent("unpaid_invoices", re.compile(
        r"\b(unpaid|owed|owing|outstanding|receivable|overdue|debtors?)\b", re.I
    ), _unpaid_invoices),
    Intent("open_orders", re.compile(
        r"\b(orders?)\b.*\b(open|waiting|pending|approval|progress)\b|"
        r"\b(open|pending|waiting)\b.*\borders?\b", re.I
    ), _open_orders),
    Intent("slow_movers", re.compile(
        r"\b(slow[- ]mov\w*|not selling|dead stock|sitting|barely (sold|moved))\b", re.I
    ), _slow_movers),
    Intent("best_sellers", re.compile(
        r"\b(best[- ]sell\w*|top (sell\w*|products?)|sells? (the )?most|fastest)\b", re.I
    ), _best_sellers),
    Intent("cold_chain", re.compile(
        r"\b(cold ?chain|fridge|temperature|excursion)\b", re.I
    ), _cold_chain),
    Intent("stock_of", re.compile(
        r"\bhow (much|many)\b|\bstock (of|level)\b|\bdo we have\b", re.I
    ), _stock_of),
    Intent("search", re.compile(r".", re.S), _search),
]


def match(question: str) -> Intent:
    for intent in INTENTS:
        if intent.pattern.search(question):
            return intent
    return INTENTS[-1]
