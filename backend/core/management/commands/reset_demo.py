"""Drop the demo tenants and reseed them.

**Destructive.** Deletes both demo organizations and everything cascading
from them — products, batches, the whole stock ledger, orders, receipts.
Requires `--yes`.

This exists because the catalogue cannot be repaired in place. A product's
packaging chain is not cosmetic: `factor_to_base` is the multiplier every
historical movement was recorded against, so editing "Pack of 100" to
"Pack of 60" silently reinterprets every quantity already in the ledger.
The old demo data was seeded with one chain applied to every product —
blisters of surgical gloves, blisters of insulin — and the only safe fix
on data that already has movements is to start again.

On real tenant data this command must never run; it is guarded to the two
demo organizations by name.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Organization

DEMO_ORGS = ["Kigali Care Pharmacy", "ABC Wholesale Pharmacy"]


class Command(BaseCommand):
    help = "Drop and reseed the demo pharmacies. Destructive."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm deletion of the demo organizations and all their data.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError(
                "This deletes both demo organizations and every record under them. "
                "Re-run with --yes if that is what you want."
            )

        with transaction.atomic():
            for name in DEMO_ORGS:
                org = Organization.objects.filter(name=name).first()
                if org is None:
                    self.stdout.write(f"  {name}: not present")
                    continue
                removed = self._purge(org)
                org.delete()
                summary = ", ".join(f"{n} {label}" for label, n in removed if n)
                self.stdout.write(self.style.WARNING(f"  deleted {name} — {summary}"))

        call_command("seed_demo")
        call_command("seed_market")
        self.stdout.write(self.style.SUCCESS("Demo data rebuilt."))

    def _purge(self, org) -> list[tuple[str, int]]:
        """Delete an organization's records, deepest dependency first.

        Almost every foreign key here is PROTECT, deliberately: real
        tenant data must not disappear because something upstream was
        removed. That protection has to be unwound explicitly rather than
        relaxed, so this walks the graph in reverse instead.
        """
        from catalog.models import (
            Category,
            Manufacturer,
            Product,
            ProductImage,
            ProductRegistration,
            ProductType,
            UnitOfMeasure,
        )
        from commerce.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
            Shipment,
            ShipmentLine,
            TradingRelationship,
            VendorListing,
        )
        from core.models import (
            AuditEvent,
            Branch,
            DocumentSequence,
            PharmacistRegistration,
            PremisesLicence,
            User,
        )
        from fiscal.models import FiscalRecord
        from inventory.models import Batch, Location, StockBalance, StockMovement
        from sales.models import (
            ControlledDeliveryEntry,
            Payment,
            Prescriber,
            Prescription,
            Patient,
            Sale,
            SaleLine,
            Shift,
            TaxRule,
            Till,
        )

        counts: list[tuple[str, int]] = []

        def wipe(label, queryset):
            n, _ = queryset.delete()
            if n:
                counts.append((label, n))

        # Sales, deepest first.
        wipe("fiscal records", FiscalRecord.objects.filter(organization=org))
        wipe("payments", Payment.objects.filter(organization=org))
        wipe("controlled entries", ControlledDeliveryEntry.objects.filter(organization=org))
        wipe("sale lines", SaleLine.objects.filter(sale__organization=org))
        wipe("sales", Sale.objects.filter(organization=org))
        wipe("shifts", Shift.objects.filter(organization=org))
        wipe("tills", Till.objects.filter(organization=org))
        wipe("prescriptions", Prescription.objects.filter(organization=org))
        wipe("prescribers", Prescriber.objects.filter(organization=org))
        wipe("patients", Patient.objects.filter(organization=org))
        wipe("tax rules", TaxRule.objects.filter(organization=org))

        # Commerce. A shipment line belongs to the supplier but points at
        # the *buyer's* order line, so purging one side alone leaves the
        # other holding a protected reference. Clear both.
        wipe("shipment lines", ShipmentLine.objects.filter(shipment__organization=org))
        wipe(
            "inbound shipment lines",
            ShipmentLine.objects.filter(order_line__order__organization=org),
        )
        wipe("shipments", Shipment.objects.filter(organization=org))
        wipe("inbound shipments", Shipment.objects.filter(order__organization=org))
        wipe("receipt lines", GoodsReceiptLine.objects.filter(receipt__organization=org))
        wipe("receipts", GoodsReceipt.objects.filter(organization=org))
        wipe("order lines", PurchaseOrderLine.objects.filter(order__organization=org))
        wipe("orders", PurchaseOrder.objects.filter(organization=org))
        # As supplier, not only as buyer.
        wipe("incoming order lines", PurchaseOrderLine.objects.filter(order__supplier=org))
        wipe("incoming orders", PurchaseOrder.objects.filter(supplier=org))
        wipe("listings", VendorListing.objects.filter(organization=org))
        wipe(
            "trading relationships",
            TradingRelationship.objects.filter(models_q(org)),
        )

        # The ledger, then what it points at.
        wipe("stock balances", StockBalance.objects.filter(organization=org))
        # StockMovement.delete() raises on purpose — the ledger is
        # append-only and a correction is a compensating movement, never
        # an erasure. A queryset delete does not call it, which is the one
        # sanctioned bypass: this is demo data being rebuilt from nothing,
        # not a correction to a real ledger.
        wipe("stock movements", StockMovement.objects.filter(organization=org))
        wipe("batches", Batch.objects.filter(organization=org))
        wipe("locations", Location.objects.filter(organization=org))

        # Catalogue.
        wipe("product images", ProductImage.objects.filter(organization=org))
        wipe("registrations", ProductRegistration.objects.filter(organization=org))
        wipe("units", UnitOfMeasure.objects.filter(organization=org))
        wipe("products", Product.objects.filter(organization=org))
        wipe("categories", Category.objects.filter(organization=org))
        wipe("manufacturers", Manufacturer.objects.filter(organization=org))
        wipe("product types", ProductType.objects.filter(organization=org))

        # Organization scaffolding.
        wipe("audit events", AuditEvent.objects.filter(organization=org))
        wipe("sequences", DocumentSequence.objects.filter(organization=org))
        wipe("pharmacists", PharmacistRegistration.objects.filter(organization=org))
        wipe("licences", PremisesLicence.objects.filter(organization=org))
        wipe("users", User.objects.filter(organization=org))
        wipe("branches", Branch.objects.filter(organization=org))
        return counts


def models_q(org):
    """A relationship names two organizations; both sides go."""
    from django.db.models import Q

    return Q(organization=org) | Q(customer=org)
