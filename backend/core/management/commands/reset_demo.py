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
                org.delete()
                self.stdout.write(self.style.WARNING(f"  deleted {name}"))

        call_command("seed_demo")
        call_command("seed_market")
        self.stdout.write(self.style.SUCCESS("Demo data rebuilt."))
