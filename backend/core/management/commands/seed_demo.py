"""Seed a demo retail pharmacy with a real catalogue.

Idempotent: safe to re-run.

The catalogue itself lives in `catalog.reference` so the retail and
wholesale seeds describe the same products, differing only in what each
licence is allowed to sell — a retail counter does not sell a carton of
1,200 capsules, and a wholesaler does not break a pack for one tablet.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import (
    Category,
    Product,
    ProductRegistration,
    ProductType,
    RegistrationStatus,
    UnitOfMeasure,
)
from catalog.reference import CATALOGUE, CATEGORIES
from core.models import Branch, LicenceKind, Organization, PremisesLicence, User
from core.quantity import Quantity
from core.tenancy import organization_scope
from inventory import services
from inventory.models import Batch, Location, LocationKind, MovementKind, TemperatureClass


class Command(BaseCommand):
    help = "Seed a demo retail pharmacy."

    @transaction.atomic
    def handle(self, *args, **options):
        # Deterministic so re-running does not reshuffle quantities and
        # make a screenshot from yesterday look wrong.
        rng = random.Random(20260812)

        org, _ = Organization.objects.get_or_create(
            name="Kigali Care Pharmacy",
            defaults={"primary_kind": LicenceKind.RETAIL_PHARMACY, "tin": "100000001"},
        )

        with organization_scope(org.id):
            branch, _ = Branch.objects.get_or_create(
                organization=org, code="MAIN", defaults={"name": "Main branch"}
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

            categories = {
                name: Category.objects.get_or_create(organization=org, name=name)[0]
                for name in CATEGORIES
            }
            types: dict[str, ProductType] = {}

            for item in CATALOGUE:
                if item.kind not in types:
                    types[item.kind] = ProductType.objects.get_or_create(
                        organization=org,
                        code=item.kind,
                        defaults={"name": item.kind.title()},
                    )[0]

                product, created = Product.objects.get_or_create(
                    organization=org, name=item.name,
                    defaults={
                        "product_type": types[item.kind],
                        "category": categories[item.category],
                        "generic_name": item.generic,
                        "brand": item.brand,
                        "legal_status": item.legal,
                        "controlled_schedule": "II" if item.legal == "CONTROLLED" else "",
                        "tax_treatment": item.tax,
                        "cold_chain": item.cold_chain,
                        "gtin": item.gtin,
                    },
                )
                if created:
                    for level in item.chain:
                        UnitOfMeasure.objects.create(
                            organization=org,
                            product=product,
                            code=level.code,
                            name=level.name,
                            factor_to_base=level.factor,
                            is_base=level.factor == 1,
                            is_purchase_default=level.code in ("PACK", "BOX", "TIN", "BOTTLE", "TUBE"),
                            # What the counter reaches for by default.
                            is_dispense_default=level.factor == 1,
                            # A retail counter cannot sell a carton.
                            is_sellable=level.retail,
                        )
                    ProductRegistration.objects.create(
                        organization=org,
                        product=product,
                        registration_number=item.registration,
                        holder=item.brand or item.generic.title(),
                        dosage_form=item.chain[-1].name,
                        registered_on=date.today() - timedelta(days=rng.randint(400, 2200)),
                        registration_expiry=date.today() + timedelta(days=rng.randint(120, 1500)),
                        status=RegistrationStatus.REGISTERED,
                    )

                # Stock: two batches for fast movers, one otherwise, with a
                # spread of expiry so FEFO and the expiry bands have
                # something real to sort.
                stock_uom = product.units.filter(
                    code__in=("PACK", "BOX", "TIN", "BOTTLE", "TUBE")
                ).first() or product.units.get(is_base=True)

                horizons = [rng.randint(400, 900)]
                if rng.random() < 0.45:
                    horizons.append(rng.randint(20, 160))

                for index, days in enumerate(horizons):
                    number = f"{_code(item.name)}-{2100 + index * 7 + len(item.name)}"
                    batch, batch_created = Batch.objects.get_or_create(
                        organization=org, product=product, supplier=None, batch_number=number,
                        defaults={
                            "expiry_date": date.today() + timedelta(days=days),
                            "unit_cost_base": item.unit_cost,
                            "cold_chain": item.cold_chain,
                        },
                    )
                    if batch_created:
                        services.post_movement(
                            organization=org,
                            location=cold if item.cold_chain else store,
                            batch=batch,
                            kind=MovementKind.OPENING,
                            quantity=Quantity(rng.randint(4, 40), stock_uom),
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
                },
            )
            if user_created:
                user.set_password("medix-demo")
                user.save()

        self.stdout.write(self.style.SUCCESS(f"Seeded {org.name}"))
        self.stdout.write(f"  categories {Category.objects.filter(organization=org).count()}")
        self.stdout.write(f"  products   {Product.objects.filter(organization=org).count()}")
        self.stdout.write(f"  batches    {Batch.objects.filter(organization=org).count()}")
        self.stdout.write("  login      marie / medix-demo")


def _code(name: str) -> str:
    """A batch prefix a pharmacist would recognise on the carton."""
    letters = [c for c in name.upper() if c.isalpha()]
    return "".join(letters[:3]) or "GEN"
