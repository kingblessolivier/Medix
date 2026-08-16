"""Expenses and write-offs.

**There is no period table here, deliberately.** The schema this was
specified against had `depot_financial_ledger` and
`retail_financial_ledger` — a row per date range with the totals already
summed into columns. Medix does not have them, for the same reason it has
no `quantity_on_hand`:

* A stored total is only true until someone backdates a credit note, and
  then it is quietly wrong with nothing to say so.
* It fixes the periods in advance. "What did I earn between the 3rd and
  the 17th" becomes unanswerable unless a job happened to bucket it that
  way.

Everything in `finance.reports` is computed for an arbitrary range from
records that already exist. See docs/28 §12.1.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import TenantModel


class ExpenseCategory(TenantModel):
    """What money went on, other than stock.

    `is_operating` separates running the pharmacy from everything else.
    Only operating expenses come off gross profit to give the estimated
    operating result; a one-off equipment purchase is not this month's
    trading performance.
    """

    name = models.CharField(max_length=80)
    code = models.CharField(max_length=30)
    is_operating = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "finance_expense_category"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_expense_category_code"
            ),
        ]

    def __str__(self) -> str:
        return self.name


#: Seeded per organization. Cold-chain power is on the list because it is
#: a real, continuous cost of holding refrigerated stock, and a depot that
#: leaves it out of its operating result is overstating what the cold
#: chain earns.
DEFAULT_CATEGORIES = [
    ("RENT", "Rent", True),
    ("SALARIES", "Salaries", True),
    ("TRANSPORT", "Transport", True),
    ("UTILITIES", "Utilities", True),
    ("COLD_CHAIN", "Cold-chain power", True),
    ("LICENCES", "Licence and regulatory fees", True),
    ("BANK", "Bank charges", True),
    ("EQUIPMENT", "Equipment", False),
    ("OTHER", "Other", True),
]


class Expense(TenantModel):
    """One cost, on the date it was incurred.

    `incurred_on`, not the date it was keyed in. A November invoice
    entered in January belongs to November, or every period report is a
    record of when somebody did their filing.
    """

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name="expenses"
    )
    branch = models.ForeignKey(
        "core.Branch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    incurred_on = models.DateField(default=timezone.localdate)
    amount = models.BigIntegerField(help_text="Minor units")
    currency = models.CharField(max_length=3, default="RWF")
    description = models.CharField(max_length=200, blank=True)
    payee = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "finance_expense"
        ordering = ["-incurred_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="ck_expense_positive"
            ),
        ]
        indexes = [models.Index(fields=["organization", "incurred_on"])]

    def __str__(self) -> str:
        return f"{self.category.name} {self.amount}"


class WriteOffReason(models.TextChoices):
    EXPIRY = "EXPIRY", "Expired"
    DAMAGE = "DAMAGE", "Damaged"
    RECALL = "RECALL", "Recalled"
    LOSS = "LOSS", "Lost or stolen"


class WriteOff(TenantModel):
    """Stock destroyed, and what it cost.

    Expired stock is the quietest way a pharmacy loses money: it was paid
    for, it sat on the shelf, and it left without a sale. It has to appear
    in the period it was written off or the operating result flatters
    itself.

    The value is captured here rather than derived later because the batch
    cost is what it was on the day — recomputing it after a later receipt
    revalues history.
    """

    number = models.CharField(max_length=30, blank=True)
    batch = models.ForeignKey("inventory.Batch", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(
        "inventory.Location", on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=10, choices=WriteOffReason.choices)
    quantity_base = models.BigIntegerField()
    unit_cost_base = models.BigIntegerField(default=0)
    value = models.BigIntegerField(default=0, help_text="quantity × unit cost, minor units")
    currency = models.CharField(max_length=3, default="RWF")
    written_off_on = models.DateField(default=timezone.localdate)

    #: A disposal is witnessed. The certificate is the artifact an
    #: inspector asks for, and a certificate with no witness is a note.
    witness_name = models.CharField(max_length=120, blank=True)
    witness_role = models.CharField(max_length=80, blank=True)

    class Meta:
        db_table = "finance_write_off"
        ordering = ["-written_off_on"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_base__gt=0), name="ck_write_off_positive"
            ),
            models.UniqueConstraint(
                fields=["organization", "number"],
                condition=~models.Q(number=""),
                name="uq_write_off_number",
            ),
        ]
        indexes = [models.Index(fields=["organization", "written_off_on"])]

    def __str__(self) -> str:
        return self.number or f"write-off {self.id}"
