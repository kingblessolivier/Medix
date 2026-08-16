"""Margin, movers and the stock that is not moving.

`test_margin_is_the_actual_batch_not_an_average` is the load-bearing one:
`SaleLine` holds the batch it was allocated from and that batch's cost,
so these are real margins on real goods.
"""

from datetime import date, timedelta

import pytest

from catalog.models import Category, LegalStatus, TaxTreatment
from core.models import Branch, User
from core.quantity import Quantity
from finance import intelligence
from inventory import services as inventory
from inventory.models import MovementKind
from sales import services as sales
from sales.models import TaxRule
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db

TODAY = date.today()
MONTH_AGO = TODAY - timedelta(days=30)


@pytest.fixture
def shop():
    org = make_org("Kigali Care")
    branch, _ = Branch.objects.get_or_create(
        organization=org, code="MAIN", defaults={"name": "Main"}
    )
    user = User.objects.create_user(username="marie", password="x", organization=org)
    location = make_location(org, "Store", "MAIN")
    TaxRule.objects.create(
        organization=org,
        treatment=TaxTreatment.STANDARD,
        rate_basis_points=1800,
        effective_from=TODAY - timedelta(days=365),
    )
    return {"org": org, "branch": branch, "user": user, "location": location}


def stocked(shop, name, *, unit_cost, packs=10, category=None, number=None):
    product = make_product(shop["org"], name, legal_status=LegalStatus.OTC)
    if category is not None:
        product.category = category
        product.save(update_fields=["category"])
    batch = make_batch(
        shop["org"], product, number=number or f"{name[:3].upper()}-1",
        unit_cost_base=unit_cost,
    )
    inventory.post_movement(
        organization=shop["org"],
        location=shop["location"],
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(packs, uom(product, "PACK")),
    )
    return product


def sell(shop, product, *, packs, price):
    """Sell and pay.

    A sale is not COMPLETED until it is settled — it sits at
    PENDING_PAYMENT, deliberately, so unpaid goods are not counted as
    revenue. These reports read completed sales, so the helper pays.
    """
    from sales import payments

    sale = sales.start_sale(
        organization=shop["org"],
        branch=shop["branch"],
        location=shop["location"],
        cashier=shop["user"],
    )
    sales.add_line(
        sale=sale,
        product=product,
        quantity=packs,
        uom=uom(product, "PACK"),
        unit_price=price,
    )
    sales.complete_sale(sale=sale, performed_by=shop["user"])
    sale.refresh_from_db()
    payments.take_payment(
        sale=sale, method="CASH", amount=sale.total, performed_by=shop["user"]
    )
    sale.refresh_from_db()
    return sale


class TestMargin:
    def test_margin_is_the_actual_batch_not_an_average(self, shop):
        """Cost comes off the line, which holds the batch FEFO chose."""
        product = stocked(shop, "Paracetamol", unit_cost=10)  # 10 per base unit
        sell(shop, product, packs=2, price=5_000)  # 200 base units

        rows = intelligence.margin_by_product(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert rows[0].revenue == 10_000
        assert rows[0].cogs == 200 * 10
        assert rows[0].gross_profit == 8_000
        assert rows[0].margin_bp == 8_000  # 80%

    def test_no_revenue_gives_no_margin_rather_than_zero(self, shop):
        row = intelligence.MarginRow(key="x", label="X", revenue=0, cogs=0)
        assert row.margin_bp is None

    def test_categories_are_grouped(self, shop):
        antibiotics = Category.objects.create(
            organization=shop["org"], name="Antibiotics"
        )
        one = stocked(shop, "Amoxicillin", unit_cost=10, category=antibiotics)
        two = stocked(shop, "Ampicillin", unit_cost=10, category=antibiotics)
        sell(shop, one, packs=1, price=3_000)
        sell(shop, two, packs=1, price=2_000)

        rows = intelligence.margin_by_category(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert len(rows) == 1
        assert rows[0].label == "Antibiotics"
        assert rows[0].revenue == 5_000

    def test_a_loss_making_line_shows_a_negative_margin(self, shop):
        """Below-cost selling is visible rather than averaged away."""
        product = stocked(shop, "Loss leader", unit_cost=100)  # 100/base
        sell(shop, product, packs=1, price=1_000)  # 100 base units cost 10,000

        rows = intelligence.margin_by_product(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert rows[0].gross_profit < 0
        assert rows[0].margin_bp < 0


class TestMovers:
    def test_best_sellers_rank_by_units_not_revenue(self, shop):
        """The stock question. Revenue would put one dear item on top."""
        cheap = stocked(shop, "Cheap fast mover", unit_cost=1, packs=50)
        dear = stocked(shop, "Dear slow mover", unit_cost=1, packs=50)
        sell(shop, cheap, packs=20, price=100)
        sell(shop, dear, packs=1, price=90_000)

        rows = intelligence.best_sellers(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert rows[0]["name"] == "Cheap fast mover"
        assert rows[0]["units"] > rows[1]["units"]
        assert rows[1]["revenue"] > rows[0]["revenue"]


class TestNotMoving:
    def test_stock_never_sold_sorts_first(self, shop):
        """The worst case, and the one an average would hide."""
        moving = stocked(shop, "Moving", unit_cost=10, packs=20)
        stocked(shop, "Never sold", unit_cost=10, packs=20)
        sell(shop, moving, packs=10, price=1_000)

        rows = intelligence.slow_movers(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert rows[0]["name"] == "Never sold"
        assert rows[0]["cover_days"] is None

    def test_capital_held_is_reported(self, shop):
        stocked(shop, "Sitting still", unit_cost=25, packs=8)
        rows = intelligence.slow_movers(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert rows[0]["value"] == 800 * 25

    def test_a_stock_out_is_something_sold_and_now_at_zero(self, shop):
        """Lost sales leave no record, so they have to be inferred."""
        product = stocked(shop, "Ran dry", unit_cost=10, packs=2)
        sell(shop, product, packs=2, price=1_000)

        rows = intelligence.stock_outs(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        )
        assert [row["name"] for row in rows] == ["Ran dry"]

    def test_something_still_held_is_not_a_stock_out(self, shop):
        product = stocked(shop, "Still there", unit_cost=10, packs=10)
        sell(shop, product, packs=1, price=1_000)
        assert intelligence.stock_outs(
            organization=shop["org"], start=MONTH_AGO, end=TODAY
        ) == []


class TestReport:
    def test_it_returns_every_section(self, shop):
        found = intelligence.report(organization=shop["org"])
        assert set(found) == {
            "start", "end", "by_category", "by_product",
            "best_sellers", "slow_movers", "stock_outs",
        }

    def test_it_defaults_to_ninety_days(self, shop):
        """A month of a new deployment has too few transactions to mean much."""
        found = intelligence.report(organization=shop["org"])
        span = date.fromisoformat(found["end"]) - date.fromisoformat(found["start"])
        assert span.days == 90
