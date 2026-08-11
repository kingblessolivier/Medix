"""Seed a demo retail pharmacy with real-shaped data.

Idempotent: safe to re-run.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import (
    LegalStatus,
    Product,
    ProductType,
    ProductTypeCode,
    TaxTreatment,
    UnitOfMeasure,
)
from core.models import Branch, LicenceKind, Organization, PremisesLicence, User
from core.quantity import Quantity
from core.tenancy import organization_scope
from inventory import services
from inventory.models import Batch, Location, LocationKind, MovementKind, TemperatureClass

CHAIN = [
    ("CARTON", "Carton of 12 packs", 1200, False),
    ("PACK", "Pack of 100", 100, False),
    ("BLISTER", "Blister of 10", 10, False),
    ("UNIT", "Unit", 1, True),
]

PRODUCTS = [
    # name, generic, legal status, tax, cold chain, batches [(number, days, cost, packs)]
    ("Amoxicillin 500mg", "amoxicillin", LegalStatus.POM, TaxTreatment.EXEMPT, False,
     [("AMX-0021", 620, 280, 5), ("AMX-0034", 240, 275, 3)]),
    ("Paracetamol 500mg", "paracetamol", LegalStatus.OTC, TaxTreatment.EXEMPT, False,
     [("PCM-1022", 900, 120, 12)]),
    ("Cetirizine 10mg", "cetirizine", LegalStatus.OTC, TaxTreatment.EXEMPT, False,
     [("CTZ-4421", 24, 95, 2)]),
    ("Insulin XYZ 100IU", "insulin", LegalStatus.POM, TaxTreatment.EXEMPT, True,
     [("INS-0084", 300, 4200, 4)]),
    ("Surgical gloves", "", LegalStatus.OTC, TaxTreatment.STANDARD, False,
     [("GLV-2210", 1400, 142, 6)]),
]


class Command(BaseCommand):
    help = "Seed a demo retail pharmacy."

    @transaction.atomic
    def handle(self, *args, **options):
        org, _ = Organization.objects.get_or_create(
            name="Kigali Care Pharmacy",
            defaults={"primary_kind": LicenceKind.RETAIL_PHARMACY, "tin": "100000001"},
        )

        with organization_scope(org.id):
            branch, _ = Branch.objects.get_or_create(
                organization=org, code="KGL", defaults={"name": "Kigali Main"}
            )
            PremisesLicence.objects.get_or_create(
                organization=org,
                number="RFDA-RET-00214",
                defaults={
                    "branch": branch,
                    "kind": LicenceKind.RETAIL_PHARMACY,
                    "issued_on": date.today() - timedelta(days=200),
                    "expiry": date.today() + timedelta(days=165),
                },
            )

            store, _ = Location.objects.get_or_create(
                organization=org, code="MAIN",
                defaults={"name": "Main Store", "kind": LocationKind.STORE, "branch": branch},
            )
            cold, _ = Location.objects.get_or_create(
                organization=org, code="COLD",
                defaults={
                    "name": "Cold room",
                    "kind": LocationKind.STORE,
                    "branch": branch,
                    "temperature_class": TemperatureClass.COLD,
                },
            )

            medicine, _ = ProductType.objects.get_or_create(
                organization=org, code=ProductTypeCode.MEDICINE, defaults={"name": "Medicine"}
            )

            for name, generic, legal, tax, cold_chain, batches in PRODUCTS:
                product, created = Product.objects.get_or_create(
                    organization=org, name=name,
                    defaults={
                        "product_type": medicine,
                        "generic_name": generic,
                        "legal_status": legal,
                        "tax_treatment": tax,
                        "cold_chain": cold_chain,
                    },
                )
                if created:
                    for code, label, factor, is_base in CHAIN:
                        UnitOfMeasure.objects.create(
                            organization=org, product=product, code=code, name=label,
                            factor_to_base=factor, is_base=is_base,
                            is_purchase_default=(code == "PACK"), is_dispense_default=is_base,
                        )

                pack = UnitOfMeasure.objects.get(product=product, code="PACK")
                location = cold if cold_chain else store

                for number, days, cost, packs in batches:
                    batch, batch_created = Batch.objects.get_or_create(
                        organization=org, product=product, supplier=None, batch_number=number,
                        defaults={
                            "expiry_date": date.today() + timedelta(days=days),
                            "unit_cost_base": cost,
                            "cold_chain": cold_chain,
                        },
                    )
                    if batch_created:
                        services.post_movement(
                            organization=org, location=location, batch=batch,
                            kind=MovementKind.OPENING,
                            quantity=Quantity(packs, pack),
                            reason="Opening balance at go-live",
                            reference="MIG-0001",
                        )

            user, user_created = User.objects.get_or_create(
                username="marie",
                defaults={
                    "organization": org,
                    "first_name": "Marie",
                    "last_name": "Uwase",
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if user_created:
                user.set_password("medix-demo")
                user.save()

        self.stdout.write(self.style.SUCCESS(f"Seeded {org.name}"))
        self.stdout.write(f"  products   {Product.objects.filter(organization=org).count()}")
        self.stdout.write(f"  batches    {Batch.objects.filter(organization=org).count()}")
        self.stdout.write(f"  login      marie / medix-demo")
