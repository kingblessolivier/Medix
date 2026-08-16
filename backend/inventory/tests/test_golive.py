"""Opening balances — the requirement that decides whether anyone can start.

The load-bearing test is `test_opening_is_not_a_purchase`. Recording
go-live stock as a receipt is the obvious shortcut and it is wrong in a
way that shows up a month later, in the margin, when nobody remembers
what caused it.
"""

from datetime import date, timedelta

import pytest

from core.exceptions import DomainError
from core.models import User
from inventory import golive, services
from inventory.models import MovementKind, StockMovement
from inventory.tests.factories import make_location, make_org, make_product

pytestmark = pytest.mark.django_db

EXPIRY = (date.today() + timedelta(days=400)).isoformat()


@pytest.fixture
def pharmacy():
    org = make_org("Kigali Care")
    user = User.objects.create_user(username="marie", password="x", organization=org)
    store = make_location(org, "Main Store", "MAIN")
    product = make_product(org, "Amoxicillin 500mg")
    other = make_product(org, "Paracetamol 500mg")
    return {"org": org, "user": user, "store": store, "product": product, "other": other}


def row(product, **overrides):
    base = {
        "product": str(product.id),
        "batch_number": "AMX-0001",
        "expiry_date": EXPIRY,
        "quantity": 4,
        "uom_code": "PACK",
        "unit_cost_base": 280,
    }
    base.update(overrides)
    return base


def load(pharmacy, rows, **kwargs):
    return golive.load_opening_balances(
        organization=pharmacy["org"],
        location=pharmacy["store"],
        rows=rows,
        performed_by=pharmacy["user"],
        **kwargs,
    )


class TestLoading:
    def test_it_puts_stock_on_the_shelf(self, pharmacy):
        loaded = load(pharmacy, [row(pharmacy["product"])])

        assert loaded.batches == 1
        assert loaded.movements == 1
        assert loaded.base_units == 400
        assert services.balance_for(
            organization=pharmacy["org"],
            product=pharmacy["product"],
            location=pharmacy["store"],
        ) == 400

    def test_opening_is_not_a_purchase(self, pharmacy):
        """The distinction this whole module exists for.

        Recording go-live stock as a receipt says the pharmacy bought all
        of it on its first day: it inflates that period's purchases,
        invents a supplier relationship, and makes the first month's
        margin meaningless.
        """
        load(pharmacy, [row(pharmacy["product"])])

        kinds = set(
            StockMovement.objects.filter(organization=pharmacy["org"]).values_list(
                "kind", flat=True
            )
        )
        assert kinds == {MovementKind.OPENING}
        assert MovementKind.PURCHASE_RECEIPT not in kinds

    def test_the_batch_carries_what_it_cost(self, pharmacy):
        """Every sale reports its margin against this number."""
        load(pharmacy, [row(pharmacy["product"], unit_cost_base=310)])

        movement = StockMovement.objects.get()
        assert movement.batch.unit_cost_base == 310

    def test_expiry_travels_with_the_batch(self, pharmacy):
        load(pharmacy, [row(pharmacy["product"])])
        assert StockMovement.objects.get().batch.expiry_date.isoformat() == EXPIRY

    def test_many_rows_load_together(self, pharmacy):
        loaded = load(
            pharmacy,
            [
                row(pharmacy["product"], batch_number="AMX-1"),
                row(pharmacy["product"], batch_number="AMX-2"),
                row(pharmacy["other"], batch_number="PAR-1"),
            ],
        )
        assert loaded.movements == 3
        assert loaded.batches == 3

    def test_the_ledger_replays_to_the_same_balance(self, pharmacy):
        """F1's promise has to hold for stock that predates the system."""
        load(pharmacy, [row(pharmacy["product"])])

        movement = StockMovement.objects.get()
        assert services.ledger_balance_for(
            organization=pharmacy["org"],
            batch=movement.batch,
            location=pharmacy["store"],
            status="AVAILABLE",
        ) == 400

    def test_a_unit_other_than_the_pack_converts(self, pharmacy):
        loaded = load(
            pharmacy, [row(pharmacy["product"], uom_code="CARTON", quantity=2)]
        )
        assert loaded.base_units == 2400


class TestItDoesNotDoubleTheShelves:
    def test_the_same_batch_twice_is_refused(self, pharmacy):
        """A second run of the same spreadsheet would double the room."""
        load(pharmacy, [row(pharmacy["product"])])
        again = load(pharmacy, [row(pharmacy["product"]), row(pharmacy["other"], batch_number="PAR-1")])

        assert again.movements == 1
        assert "already on this shelf" in again.skipped[0]["reason"]

    def test_the_balance_is_unchanged_by_the_second_run(self, pharmacy):
        load(pharmacy, [row(pharmacy["product"])])
        load(pharmacy, [row(pharmacy["product"]), row(pharmacy["other"], batch_number="PAR-1")])

        assert services.balance_for(
            organization=pharmacy["org"],
            product=pharmacy["product"],
            location=pharmacy["store"],
        ) == 400

    def test_a_second_branch_can_still_open(self, pharmacy):
        """The guard is per shelf, not per pharmacy. Opening a new branch
        next year is not a second go-live."""
        load(pharmacy, [row(pharmacy["product"])])
        annex = make_location(pharmacy["org"], "Remera branch", "RMR")

        loaded = golive.load_opening_balances(
            organization=pharmacy["org"],
            location=annex,
            rows=[row(pharmacy["product"])],
            performed_by=pharmacy["user"],
        )
        assert loaded.movements == 1


class TestRowsItRefuses:
    @pytest.mark.parametrize(
        "override,reason",
        [
            ({"batch_number": ""}, "No batch number"),
            ({"expiry_date": None}, "No expiry date"),
            ({"quantity": 0}, "Quantity must be positive"),
            ({"quantity": -3}, "Quantity must be positive"),
        ],
    )
    def test_a_bad_row_is_named_not_guessed(self, pharmacy, override, reason):
        """The row number goes back so a spreadsheet can be corrected."""
        loaded = load(
            pharmacy,
            [row(pharmacy["product"], **override), row(pharmacy["other"], batch_number="PAR-1")],
        )
        assert loaded.skipped == [{"row": 1, "reason": reason}]
        assert loaded.movements == 1

    def test_a_batch_with_no_number_cannot_be_recalled(self, pharmacy):
        """Which is why it is refused rather than given a generated one."""
        with pytest.raises(DomainError):
            load(pharmacy, [row(pharmacy["product"], batch_number="   ")])

    def test_an_unknown_product_is_skipped(self, pharmacy):
        import uuid

        loaded = load(
            pharmacy,
            [
                {"product": str(uuid.uuid4()), "batch_number": "X", "expiry_date": EXPIRY,
                 "quantity": 1},
                row(pharmacy["other"], batch_number="PAR-1"),
            ],
        )
        assert loaded.skipped[0]["reason"] == "Unknown product"

    def test_an_unknown_unit_is_an_error_not_a_guess(self, pharmacy):
        with pytest.raises(DomainError) as raised:
            load(pharmacy, [row(pharmacy["product"], uom_code="CRATE")])
        assert raised.value.code == "unknown_uom"

    def test_a_load_where_nothing_landed_fails(self, pharmacy):
        """A load that put nothing on the shelves is not a success."""
        with pytest.raises(DomainError) as raised:
            load(pharmacy, [row(pharmacy["product"], quantity=0)])
        assert raised.value.code == "nothing_loaded"

    def test_and_leaves_no_half_written_batches(self, pharmacy):
        from inventory.models import Batch

        with pytest.raises(DomainError):
            load(pharmacy, [row(pharmacy["product"], quantity=0)])
        assert not Batch.objects.exists()

    def test_an_empty_load_is_refused(self, pharmacy):
        with pytest.raises(DomainError) as raised:
            load(pharmacy, [])
        assert raised.value.code == "no_rows"

    def test_another_tenants_location_is_refused(self, pharmacy):
        other = make_org("ABC Wholesale", kind="WHOLESALE")
        theirs = make_location(other, "Depot", "DEP")

        with pytest.raises(DomainError) as raised:
            golive.load_opening_balances(
                organization=pharmacy["org"],
                location=theirs,
                rows=[row(pharmacy["product"])],
                performed_by=pharmacy["user"],
            )
        assert raised.value.code == "not_yours"


class TestApi:
    @pytest.fixture
    def client(self, pharmacy):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=pharmacy["user"])
        return client

    def test_it_loads_through_the_endpoint(self, client, pharmacy):
        response = client.post(
            "/api/v1/stock/opening/",
            {
                "location": str(pharmacy["store"].id),
                "rows": [row(pharmacy["product"])],
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["movements"] == 1
        assert response.data["base_units"] == 400

    def test_it_reports_the_rows_it_skipped(self, client, pharmacy):
        response = client.post(
            "/api/v1/stock/opening/",
            {
                "location": str(pharmacy["store"].id),
                "rows": [
                    row(pharmacy["product"], batch_number=""),
                    row(pharmacy["other"], batch_number="PAR-1"),
                ],
            },
            format="json",
        )
        assert response.data["skipped"] == [{"row": 1, "reason": "No batch number"}]

    def test_another_tenants_location_is_not_found(self, client):
        other = make_org("ABC Wholesale", kind="WHOLESALE")
        theirs = make_location(other, "Depot", "DEP")

        response = client.post(
            "/api/v1/stock/opening/",
            {"location": str(theirs.id), "rows": []},
            format="json",
        )
        assert response.status_code == 404
