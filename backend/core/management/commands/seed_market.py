"""Seed a wholesale pharmacy with listings, so the marketplace is real.

Idempotent: safe to re-run.

Same catalogue as the retail seed, different sellability: a wholesaler
trades cartons and packs and does not break a pack for one tablet. Both
sides carry the same Rwanda FDA registration number, which is what lets
a receiving pharmacy match a bought product to its own catalogue row
instead of forking a duplicate.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import (
    Category,
    Manufacturer,
    Product,
    ProductRegistration,
    ProductType,
    RegistrationStatus,
    UnitOfMeasure,
)
from catalog.reference import (
    CATALOGUE,
    CATEGORIES,
    MANUFACTURERS,
    form_for,
    manufacturer_for,
    route_for,
    storage_for,
    strength_for,
)
from commerce.models import Availability, TradingRelationship
from commerce.services import publish_listing
from core.models import Branch, LicenceKind, Organization, PremisesLicence, User
from core.quantity import Quantity
from core.tenancy import organization_scope
from inventory import services as inventory
from inventory.models import Batch, Location, LocationKind, MovementKind, TemperatureClass


class Command(BaseCommand):
    help = "Seed a wholesale pharmacy and its marketplace listings."

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(4417)

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
            cold, _ = Location.objects.get_or_create(
                organization=wholesale, code="WCOLD",
                defaults={
                    "name": "Depot cold room",
                    "kind": LocationKind.STORE,
                    "branch": branch,
                    "temperature_class": TemperatureClass.COLD,
                },
            )

            categories = {
                name: Category.objects.get_or_create(organization=wholesale, name=name)[0]
                for name in CATEGORIES
            }
            houses = {
                house: Manufacturer.objects.get_or_create(
                    organization=wholesale,
                    name=house,
                    defaults={"country_of_origin": country, "gmp_certified": gmp},
                )[0]
                for house, country, gmp in MANUFACTURERS
            }
            types: dict[str, ProductType] = {}

            for index, item in enumerate(CATALOGUE):
                if item.kind not in types:
                    types[item.kind] = ProductType.objects.get_or_create(
                        organization=wholesale,
                        code=item.kind,
                        defaults={"name": item.kind.title()},
                    )[0]

                product, created = Product.objects.get_or_create(
                    organization=wholesale, name=item.name,
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
                        "dosage_form": form_for(item),
                        "strength": strength_for(item),
                        "route": route_for(item),
                        "manufacturer": houses[manufacturer_for(item)],
                        "storage_min_c": storage_for(item)[0],
                        "storage_max_c": storage_for(item)[1],
                        "light_sensitive": item.kind == "MEDICINE" and item.cold_chain,
                    },
                )
                if created:
                    for level in item.chain:
                        UnitOfMeasure.objects.create(
                            organization=wholesale,
                            product=product,
                            code=level.code,
                            name=level.name,
                            factor_to_base=level.factor,
                            is_base=level.factor == 1,
                            is_purchase_default=level.code == "CARTON",
                            is_dispense_default=level.factor == 1,
                            # A wholesaler will not break a pack.
                            is_sellable=level.wholesale,
                        )
                    ProductRegistration.objects.create(
                        organization=wholesale,
                        product=product,
                        registration_number=item.registration,
                        holder=item.brand or item.generic.title(),
                        dosage_form=item.chain[-1].name,
                        registered_on=date.today() - timedelta(days=rng.randint(400, 2200)),
                        registration_expiry=date.today() + timedelta(days=rng.randint(120, 1500)),
                        status=RegistrationStatus.REGISTERED,
                    )

                # The trade unit: what the listing is priced in.
                trade_uom = product.units.filter(
                    code__in=("PACK", "BOX", "TIN", "BOTTLE", "TUBE")
                ).first() or product.units.get(is_base=True)

                # A real marketplace is not uniformly in stock. Every
                # eighth line is incoming, every eleventh import-only —
                # so the browse screen has to show the states a buyer
                # actually meets, not one happy row repeated.
                if index % 11 == 10:
                    availability = Availability.IMPORT_ON_DEMAND
                elif index % 8 == 7:
                    availability = Availability.INCOMING
                else:
                    availability = Availability.AVAILABLE_NOW

                if availability == Availability.AVAILABLE_NOW:
                    batch, batch_created = Batch.objects.get_or_create(
                        organization=wholesale, product=product, supplier=None,
                        batch_number=f"{_code(item.name)}-W{1000 + index}",
                        defaults={
                            "expiry_date": date.today() + timedelta(days=rng.randint(300, 900)),
                            "unit_cost_base": int(item.unit_cost * 0.82),
                            "cold_chain": item.cold_chain,
                        },
                    )
                    if batch_created:
                        inventory.post_movement(
                            organization=wholesale,
                            location=cold if item.cold_chain else depot,
                            batch=batch,
                            kind=MovementKind.OPENING,
                            quantity=Quantity(rng.randint(20, 200), trade_uom),
                            reason="Opening balance",
                        )

                publish_listing(
                    organization=wholesale,
                    product=product,
                    price=item.pack_price,
                    price_uom=trade_uom,
                    availability=availability,
                    moq=rng.choice([1, 2, 5, 10, 20]),
                    lead_time_days=rng.choice([1, 1, 2, 3, 7, 21]),
                )

            jean, created = User.objects.get_or_create(
                username="jean",
                defaults={
                    "organization": wholesale,
                    "first_name": "Jean",
                    "last_name": "Bizimana",
                    "is_staff": True,
                },
            )
            if created:
                jean.set_password("medix-demo")
                jean.save()

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
        self.stdout.write(f"  products   {Product.objects.filter(organization=wholesale).count()}")
        self.stdout.write(
            f"  listings   {VendorListing.objects.filter(organization=wholesale).count()}"
        )
        self.stdout.write("  login      jean / medix-demo")


def _code(name: str) -> str:
    letters = [c for c in name.upper() if c.isalpha()]
    return "".join(letters[:3]) or "GEN"
