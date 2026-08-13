"""Inventory: locations, batches, and the stock ledger.

**There is no mutable quantity column.** Stock is an append-only sequence
of movements; balances are derived. `StockBalance` is a projection that
can be rebuilt from the ledger at any time, and when the two disagree the
ledger is right.

See docs/03-data-model.md and ADR-001.
"""

from __future__ import annotations

from django.db import models

from catalog.models import Product
from core.models import BaseModel, TenantModel


class TemperatureClass(models.TextChoices):
    AMBIENT = "AMBIENT", "Ambient"
    COOL = "COOL_15_25", "Cool 15–25°C"
    COLD = "COLD_2_8", "Cold 2–8°C"
    FROZEN = "FROZEN", "Frozen"


class LocationKind(models.TextChoices):
    BRANCH = "BRANCH", "Branch"
    STORE = "STORE", "Store"


class StockStatus(models.TextChoices):
    """Status is not quantity.

    A batch may hold quantity in several statuses at once. Only AVAILABLE
    is sellable.
    """

    AVAILABLE = "AVAILABLE", "Available"
    RESERVED = "RESERVED", "Reserved"
    QUARANTINED = "QUARANTINED", "Quarantined"
    DAMAGED = "DAMAGED", "Damaged"
    EXPIRED = "EXPIRED", "Expired"
    RECALLED = "RECALLED", "Recalled"
    IN_TRANSIT = "IN_TRANSIT", "In transit"
    RETURNED = "RETURNED", "Returned"


class MovementKind(models.TextChoices):
    OPENING = "OPENING", "Opening balance"
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT", "Purchase receipt"
    SALE = "SALE", "Sale"
    WHOLESALE_DISPATCH = "WHOLESALE_DISPATCH", "Wholesale dispatch"
    SALE_RETURN = "SALE_RETURN", "Sale return"
    TRANSFER_OUT = "TRANSFER_OUT", "Transfer out"
    TRANSFER_IN = "TRANSFER_IN", "Transfer in"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"
    DISPOSAL = "DISPOSAL", "Disposal"
    QUARANTINE = "QUARANTINE", "Quarantine"
    RELEASE = "RELEASE", "Release"
    RECALL = "RECALL", "Recall"
    EXPIRY_WRITE_OFF = "EXPIRY_WRITE_OFF", "Expiry write-off"
    SUPPLIER_RETURN = "SUPPLIER_RETURN", "Supplier return"


class Location(TenantModel):
    """Organization → branch → store.

    ``temperature_class`` is enforced: a cold-chain batch cannot be placed
    in an ambient location.
    """

    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    branch = models.ForeignKey(
        "core.Branch", null=True, blank=True, on_delete=models.PROTECT, related_name="locations"
    )
    kind = models.CharField(max_length=10, choices=LocationKind.choices)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20)
    temperature_class = models.CharField(
        max_length=12, choices=TemperatureClass.choices, default=TemperatureClass.AMBIENT
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_location"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uq_location_code"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_cold_capable(self) -> bool:
        return self.temperature_class in (TemperatureClass.COLD, TemperatureClass.FROZEN)


class Batch(TenantModel):
    """A manufactured lot. Traceability and costing both hang off this.

    ``unit_cost_base`` is the **landed** cost per base unit — product price
    plus apportioned freight, duty and clearance — not the invoice price.
    Every margin figure in the system derives from it.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="batches")
    supplier = models.ForeignKey(
        "core.Organization", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    batch_number = models.CharField(max_length=60)
    manufacture_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField()

    unit_cost_base = models.BigIntegerField(
        default=0, help_text="Landed cost per base unit, minor units"
    )
    cost_currency = models.CharField(max_length=3, default="RWF")
    landed_cost_note = models.TextField(blank=True)

    gtin = models.CharField(max_length=14, blank=True, help_text="GS1 (01)")
    serial = models.CharField(max_length=40, blank=True, help_text="GS1 (21)")
    cold_chain = models.BooleanField(default=False)

    class Meta:
        db_table = "inventory_batch"
        ordering = ["expiry_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product", "supplier", "batch_number"],
                name="uq_batch_number",
            ),
            models.CheckConstraint(
                condition=models.Q(manufacture_date__isnull=True)
                | models.Q(expiry_date__gt=models.F("manufacture_date")),
                name="ck_batch_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost_base__gte=0), name="ck_batch_cost_nonneg"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "expiry_date"]),
            models.Index(fields=["organization", "product", "expiry_date"]),
        ]

    def __str__(self) -> str:
        return self.batch_number


class StockMovement(BaseModel):
    """Append-only. Never updated, never deleted.

    The only write path is `inventory.services.post_movement()`. In
    production the UPDATE and DELETE grants are revoked on this table —
    application discipline is good, a revoked grant is better.
    """

    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="movements")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=12, choices=StockStatus.choices, default=StockStatus.AVAILABLE
    )

    kind = models.CharField(max_length=20, choices=MovementKind.choices)
    quantity_base = models.BigIntegerField(help_text="Signed, in the product's base unit")
    balance_after_base = models.BigIntegerField()

    reason = models.TextField(blank=True)
    reference = models.CharField(
        max_length=60, blank=True, help_text="Human-facing document number"
    )
    performed_by = models.ForeignKey(
        "core.User", null=True, on_delete=models.PROTECT, related_name="+"
    )

    occurred_at = models.DateTimeField(help_text="Business time")
    recorded_at = models.DateTimeField(auto_now_add=True, help_text="System receipt time")
    idempotency_key = models.CharField(max_length=64)

    class Meta:
        db_table = "inventory_stock_movement"
        ordering = ["-occurred_at", "-recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"], name="uq_movement_idempotency"
            ),
            models.CheckConstraint(
                condition=~models.Q(quantity_base=0), name="ck_movement_qty_nonzero"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "batch", "occurred_at"]),
            models.Index(fields=["organization", "product", "-occurred_at"]),
            models.Index(fields=["organization", "location", "occurred_at"]),
            models.Index(fields=["batch", "kind"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.quantity_base:+d}"

    def save(self, *args, **kwargs):
        if self.pk and StockMovement.objects.filter(pk=self.pk).exists():
            raise RuntimeError(
                "StockMovement is append-only. Post a compensating movement instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(
            "StockMovement is append-only. Post a compensating movement instead."
        )


class StockBalance(BaseModel):
    """Materialized projection of the ledger. Disposable and rebuildable.

    If this disagrees with the sum of movements, the ledger is right —
    run `rebuild_balances()` and find the code path that wrote here
    directly.
    """

    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="+"
    )
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="balances")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="balances")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=12, choices=StockStatus.choices)

    quantity_base = models.BigIntegerField(default=0)
    expiry_date = models.DateField(help_text="Denormalized from batch so FEFO stays index-only")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_stock_balance"
        constraints = [
            models.UniqueConstraint(
                fields=["location", "batch", "status"], name="uq_balance"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_base__gte=0), name="ck_balance_nonneg"
            ),
        ]
        indexes = [
            # The FEFO query: nearest expiry first, available and non-empty.
            models.Index(
                fields=["organization", "product", "location", "expiry_date"],
                name="idx_balance_fefo",
                condition=models.Q(status="AVAILABLE", quantity_base__gt=0),
            ),
            models.Index(fields=["organization", "expiry_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch_id} {self.status} {self.quantity_base}"


class Allocation:
    """One batch's share of a requested issue. Not persisted."""

    __slots__ = ("batch", "quantity_base")

    def __init__(self, batch: Batch, quantity_base: int) -> None:
        self.batch = batch
        self.quantity_base = quantity_base

    def __repr__(self) -> str:
        return f"Allocation({self.batch.batch_number!r}, {self.quantity_base})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, Allocation)
            and other.batch_id == self.batch.id
            and other.quantity_base == self.quantity_base
        )


# --------------------------------------------------------------------------
# Cold chain
#
# Declared here rather than beside their logic in `inventory/telemetry.py`
# for the reason `core/models.py` records: Django registers a model when
# the module defining it is imported, and a lazily-imported module makes
# the app registry depend on import order.
# --------------------------------------------------------------------------

from inventory.telemetry import Excursion, Reading, Sensor  # noqa: E402,F401
