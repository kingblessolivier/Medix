"""Seed a wholesale pharmacy with listings, so the marketplace is real.

Idempotent: safe to re-run.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import (
    LegalStatus,
    Product,
    ProductType,
    ProductTypeCode,
    TaxTreatment,
    UnitOfMeasure,
)
from commerce.models import Availability, TradingRelationship
from commerce.services import publish_listing
from core.models import Branch, LicenceKind, Organization, PremisesLicence, User
from core.quantity import Quantity
from core.tenancy import organization_scope
from inventory import services as inventory
from inventory.models import Batch, Location, LocationKind, MovementKind

CHAIN = [
    ("CARTON", "Carton of 12 packs", 1200, False),
    ("PACK", "Pack of 100", 100, False),
    ("BLISTER", "Blister of 10", 10, False),
    ("UNIT", "Unit", 1, True),
]

CATALOGUE = [
    # name, generic, legal, tax, cold, price, moq, lead days, availability, packs
    ("Amoxicillin 500mg", "amoxicillin", LegalStatus.POM, TaxTreatment.EXEMPT, False,
     28000, 10, 1, Availability.AVAILABLE_NOW, 40),
    ("Paracetamol 500mg", "paracetamol", LegalStatus.OTC, TaxTreatment.EXEMPT, False,
     12000, 20, 1, Availability.AVAILABLE_NOW, 80),
    ("Cetirizine 10mg", "cetirizine", LegalStatus.OTC, TaxTreatment.EXEMPT, False,
     9500, 10, 2, Availability.AVAILABLE_NOW, 25),
    ("Surgical gloves", "", LegalStatus.OTC, TaxTreatment.STANDARD, False,
     14200, 5, 1, Availability.AVAILABLE_NOW, 60),
    ("Insulin XYZ 100IU", "insulin", LegalStatus.POM, TaxTreatment.EXEMPT, True,
     42000, 4, 21, Availability.INCOMING, 0),
    ("Rare Antiviral 200mg", "antiviral", LegalStatus.POM, TaxTreatment.EXEMPT, False,
     186000, 100, 45, Availability.IMPORT_ON_DEMAND, 0),
]


class Command(BaseCommand):
    help = "Seed a wholesale pharmacy and its marketplace listings."

    @transaction.atomic
    def handle(self, *args, **options):
        wholesale, _ = Organization.objects.get_or_create(
            name="ABC Wholesale Pharmacy",
            defaults={"primary_kind": LicenceKind.WHOLESALE_PHARMACY, "tin": "100000002"},
        )

        with organization_scope(wholesale.id):
            branch, _ = Branch.objects.get_or_create(
                organization=wholesale, code="DEP", defaults={"name": "ABC Depot"}
            )
            PremisesLicence.objects.get_or_create(
                organization=wholesale,
                number="RFDA-WHL-00318",
                defaults={
                    "branch": branch,
                    "kind": LicenceKind.WHOLESALE_PHARMACY,
                    "issued_on": date.today() - timedelta(days=300),
                    "expiry": date.today() + timedelta(days=400),
                },
            )
            depot, _ = Location.objects.get_or_create(
                organization=wholesale, code="DEPOT",
                defaults={"name": "ABC Depot", "kind": LocationKind.STORE, "branch": branch},
            )
            medicine, _ = ProductType.objects.get_or_create(
                organization=wholesale, code=ProductTypeCode.MEDICINE,
                defaults={"name": "Medicine"},
            )

            for (name, generic, legal, tax, cold, price, moq, lead,
                 availability, packs) in CATALOGUE:
                product, created = Product.objects.get_or_create(
                    organization=wholesale, name=name,
                    defaults={
                        "product_type": medicine,
                        "generic_name": generic,
                        "legal_status": legal,
                        "tax_treatment": tax,
                        "cold_chain": cold,
                    },
                )
                if created:
                    for code, label, factor, is_base in CHAIN:
                        UnitOfMeasure.objects.create(
                            organization=wholesale, product=product, code=code, name=label,
                            factor_to_base=factor, is_base=is_base,
                            is_purchase_default=(code == "PACK"), is_dispense_default=is_base,
                        )

                pack = UnitOfMeasure.objects.get(product=product, code="PACK")

                if packs:
                    batch, batch_created = Batch.objects.get_or_create(
                        organization=wholesale, product=product, supplier=None,
                        batch_number=f"{name[:3].upper()}-W001",
                        defaults={
                            "expiry_date": date.today() + timedelta(days=700),
                            "unit_cost_base": price // 100,
                            "cold_chain": cold,
                        },
                    )
                    if batch_created:
                        inventory.post_movement(
                            organization=wholesale, location=depot, batch=batch,
                            kind=MovementKind.OPENING,
                            quantity=Quantity(packs, pack),
                            reason="Opening balance",
                        )

                publish_listing(
                    organization=wholesale, product=product, price=price,
                    price_uom=pack, availability=availability, moq=moq,
                    lead_time_days=lead,
                )

            User.objects.get_or_create(
                username="jean",
                defaults={"organization": wholesale, "first_name": "Jean", "last_name": "Bizimana"},
            )

            retail = Organization.objects.filter(name="Kigali Care Pharmacy").first()
            if retail:
                TradingRelationship.objects.get_or_create(
                    organization=wholesale, customer=retail,
                    defaults={
                        "is_verified": True,
                        "verified_at": timezone.now(),
                        "credit_limit": 5_000_000,
                        "payment_terms_days": 30,
                    },
                )

        from commerce.models import VendorListing

        self.stdout.write(self.style.SUCCESS(f"Seeded {wholesale.name}"))
        self.stdout.write(f"  listings {VendorListing.objects.filter(organization=wholesale).count()}")
