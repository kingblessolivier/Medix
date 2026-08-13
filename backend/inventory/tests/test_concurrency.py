"""Concurrent issue must not oversell.

Sequential calls prove nothing here — the row lock in _lock_balance is
only exercised by real parallel transactions. This is the test that would
have caught a missing select_for_update.
"""

import threading

import pytest
from django.db import connections, transaction

from core.exceptions import InsufficientStock
from core.quantity import Quantity
from inventory import services
from inventory.models import MovementKind, StockBalance, StockMovement, StockStatus
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db(transaction=True)


def test_parallel_issues_cannot_oversell():
    """Ten threads each try to take 150 units from a stock of 1,000.

    Six can succeed (900), the rest must fail. The total issued must never
    exceed what existed, and the balance must never go negative.
    """
    org = make_org()
    location = make_location(org)
    product = make_product(org)
    batch = make_batch(org, product)

    services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(10, uom(product, "PACK")),
    )

    succeeded: list[int] = []
    failed: list[int] = []
    lock = threading.Lock()

    def issue(index: int) -> None:
        try:
            with transaction.atomic():
                services.post_movement(
                    organization=org,
                    location=location,
                    batch=batch,
                    kind=MovementKind.SALE,
                    quantity=Quantity(150, uom(product, "UNIT")),
                    idempotency_key=f"thread-{index}",
                )
            with lock:
                succeeded.append(index)
        except InsufficientStock:
            with lock:
                failed.append(index)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=issue, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    balance = StockBalance.objects.get(
        batch=batch, location=location, status=StockStatus.AVAILABLE
    )

    assert len(succeeded) == 6, f"expected 6 successes, got {len(succeeded)}"
    assert len(failed) == 4
    assert balance.quantity_base == 100
    assert balance.quantity_base >= 0

    # The ledger and the projection must still agree afterwards.
    ledger = services.ledger_balance_for(
        organization=org, batch=batch, location=location, status=StockStatus.AVAILABLE
    )
    assert ledger == balance.quantity_base


def test_parallel_same_idempotency_key_applies_once():
    """Two threads racing with the same key must produce one movement."""
    org = make_org()
    location = make_location(org)
    product = make_product(org)
    batch = make_batch(org, product)

    services.post_movement(
        organization=org,
        location=location,
        batch=batch,
        kind=MovementKind.PURCHASE_RECEIPT,
        quantity=Quantity(5, uom(product, "PACK")),
    )

    errors: list[Exception] = []

    def issue() -> None:
        try:
            with transaction.atomic():
                services.post_movement(
                    organization=org,
                    location=location,
                    batch=batch,
                    kind=MovementKind.SALE,
                    quantity=Quantity(10, uom(product, "UNIT")),
                    idempotency_key="same-key",
                )
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=issue) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The loser must report the winner's result, not raise. An agent
    # retrying after a dropped connection must never see an error for
    # work that actually succeeded.
    assert errors == [], f"racing retry raised instead of replaying: {errors}"

    sales = StockMovement.objects.filter(batch=batch, kind=MovementKind.SALE)
    assert sales.count() == 1, f"idempotency key applied {sales.count()} times"
    assert services.balance_for(organization=org, product=product) == 490
