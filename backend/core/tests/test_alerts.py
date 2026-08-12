"""The alert framework.

The boundary tests matter most. A threshold that fires at 89 days but not
at 90 is a different rule from the one the documentation states, and the
difference only shows up in an argument with a regulator.
"""

from datetime import date, timedelta

import pytest

from core import alerts
from core.alerts import (
    AcknowledgementRequired,
    Alert,
    AlertAcknowledgement,
    AlertBlocked,
    AlertRule,
    Severity,
    enforce,
    rule_for,
    seed_alert_rules,
    summarise,
)
from core.models import AuditEvent, User
from core.quantity import Quantity
from inventory import checks as inventory_checks
from inventory import services as inventory
from inventory.models import MovementKind
from inventory.tests.factories import make_batch, make_location, make_org, make_product, uom

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    organization = make_org("Kigali Care")
    User.objects.create_user(username="marie", password="x", organization=organization)
    return organization


@pytest.fixture
def actor(org):
    return User.objects.get(username="marie")


def warning(code="TEST_WARNING"):
    return Alert(code=code, severity=Severity.WARNING, title="Careful")


def critical(code="TEST_CRITICAL"):
    return Alert(code=code, severity=Severity.CRITICAL, title="Stop")


def info(code="TEST_INFO"):
    return Alert(code=code, severity=Severity.INFO, title="By the way")


class TestSeverityIsBehaviour:
    def test_critical_raises(self, org, actor):
        with pytest.raises(AlertBlocked):
            enforce([critical()], organization=org, performed_by=actor)

    def test_critical_cannot_be_acknowledged_past(self, org, actor):
        """No 'proceed anyway'. A case needing an override is a warning."""
        with pytest.raises(AlertBlocked):
            enforce(
                [critical()],
                organization=org,
                performed_by=actor,
                acknowledged=["TEST_CRITICAL"],
            )

    def test_a_warning_refuses_until_acknowledged(self, org, actor):
        with pytest.raises(AcknowledgementRequired):
            enforce([warning()], organization=org, performed_by=actor)

    def test_an_acknowledged_warning_passes(self, org, actor):
        enforce(
            [warning()],
            organization=org,
            performed_by=actor,
            acknowledged=["TEST_WARNING"],
        )

    def test_acknowledging_a_different_code_does_not_help(self, org, actor):
        """Codes, not a boolean — tomorrow's check is not pre-accepted."""
        with pytest.raises(AcknowledgementRequired):
            enforce(
                [warning("ONE")],
                organization=org,
                performed_by=actor,
                acknowledged=["TWO"],
            )

    def test_info_never_interrupts(self, org, actor):
        returned = enforce([info()], organization=org, performed_by=actor)
        assert [alert.code for alert in returned] == ["TEST_INFO"]

    def test_criticals_are_reported_together(self, org, actor):
        """One round trip, not one per blocker."""
        with pytest.raises(AlertBlocked) as raised:
            enforce(
                [critical("ONE"), critical("TWO")], organization=org, performed_by=actor
            )
        assert len(raised.value.meta["alerts"]) == 2


class TestAcknowledgementIsRecorded:
    def test_a_row_is_written(self, org, actor):
        enforce(
            [warning()],
            organization=org,
            performed_by=actor,
            acknowledged=["TEST_WARNING"],
            reason="Customer waiting.",
        )
        record = AlertAcknowledgement.objects.get(code="TEST_WARNING")
        assert record.acknowledged_by_id == actor.id
        assert record.reason == "Customer waiting."

    def test_the_audit_stream_gets_it_too(self, org, actor):
        """An override nobody can trace is not a control."""
        enforce(
            [warning()],
            organization=org,
            performed_by=actor,
            acknowledged=["TEST_WARNING"],
        )
        assert AuditEvent.objects.filter(action="core.alert.acknowledged").exists()

    def test_nothing_is_recorded_when_it_was_refused(self, org, actor):
        with pytest.raises(AcknowledgementRequired):
            enforce([warning()], organization=org, performed_by=actor)
        assert not AlertAcknowledgement.objects.exists()


class TestThresholdsAreDated:
    def test_the_default_applies_with_nothing_configured(self, org):
        rule = rule_for(organization=org, code="SHORT_DATED_BATCH")
        assert rule["threshold"]["days"] == 90

    def test_a_configured_rule_wins(self, org):
        AlertRule.objects.create(
            organization=org,
            code="SHORT_DATED_BATCH",
            severity=Severity.WARNING,
            threshold={"days": 120},
            effective_from=date.today() - timedelta(days=10),
        )
        assert rule_for(organization=org, code="SHORT_DATED_BATCH")["threshold"]["days"] == 120

    def test_history_reads_under_the_rule_that_applied_then(self, org):
        """The whole reason these are dated rather than a settings value."""
        AlertRule.objects.create(
            organization=org,
            code="SHORT_DATED_BATCH",
            severity=Severity.WARNING,
            threshold={"days": 60},
            effective_from=date.today() - timedelta(days=200),
            effective_to=date.today() - timedelta(days=100),
        )
        AlertRule.objects.create(
            organization=org,
            code="SHORT_DATED_BATCH",
            severity=Severity.WARNING,
            threshold={"days": 120},
            effective_from=date.today() - timedelta(days=99),
        )
        then = rule_for(
            organization=org,
            code="SHORT_DATED_BATCH",
            as_of=date.today() - timedelta(days=150),
        )
        now = rule_for(organization=org, code="SHORT_DATED_BATCH")
        assert then["threshold"]["days"] == 60
        assert now["threshold"]["days"] == 120

    def test_a_future_rule_does_not_apply_yet(self, org):
        AlertRule.objects.create(
            organization=org,
            code="SHORT_DATED_BATCH",
            severity=Severity.WARNING,
            threshold={"days": 30},
            effective_from=date.today() + timedelta(days=10),
        )
        assert rule_for(organization=org, code="SHORT_DATED_BATCH")["threshold"]["days"] == 90

    def test_seeding_materialises_the_defaults(self, org):
        created = seed_alert_rules(org)
        assert len(created) == len(alerts.DEFAULTS)
        assert AlertRule.objects.filter(organization=org).count() == len(alerts.DEFAULTS)

    def test_seeding_twice_does_not_duplicate(self, org):
        seed_alert_rules(org)
        seed_alert_rules(org)
        assert AlertRule.objects.filter(organization=org).count() == len(alerts.DEFAULTS)


class TestShortDatedBoundary:
    """89, 90, 91 — the difference between the documented rule and another."""

    def stock(self, org, *, days, number):
        product = make_product(org, f"Product {number}")
        location = make_location(org, "Store", f"L{number}")
        batch = make_batch(org, product, number=number, expires_in_days=days)
        inventory.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(product, "PACK")),
        )
        return batch

    def test_inside_the_window_fires(self, org):
        self.stock(org, days=89, number="A")
        found = inventory_checks.short_dated_batches(organization=org)
        assert [alert.meta["days"] for alert in found] == [89]

    def test_exactly_at_the_window_fires(self, org):
        self.stock(org, days=90, number="B")
        assert len(inventory_checks.short_dated_batches(organization=org)) == 1

    def test_outside_the_window_is_silent(self, org):
        self.stock(org, days=91, number="C")
        assert inventory_checks.short_dated_batches(organization=org) == []

    def test_a_batch_with_no_stock_left_is_not_a_warning(self, org):
        """History, not a warning. Warning about it teaches clicking through."""
        product = make_product(org, "Gone")
        # A batch with an expiry but no movement: no balance row exists,
        # so there is nothing to warn about.
        make_batch(org, product, number="EMPTY", expires_in_days=30)
        assert inventory_checks.short_dated_batches(organization=org) == []

    def test_the_soonest_expiry_is_listed_first(self, org):
        self.stock(org, days=80, number="LATER")
        self.stock(org, days=10, number="SOONER")
        found = inventory_checks.short_dated_batches(organization=org)
        assert [alert.meta["days"] for alert in found] == [10, 80]


class TestReorderPoint:
    """The field has existed since the catalogue landed; nothing read it."""

    def test_below_the_point_warns(self, org):
        product = make_product(org, "Amoxicillin")
        product.reorder_point_base = 1_000
        product.save(update_fields=["reorder_point_base"])
        location = make_location(org, "Store", "MAIN")
        batch = make_batch(org, product, number="A1")
        inventory.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(product, "PACK")),  # 500 base
        )
        found = inventory_checks.below_reorder_point(organization=org)
        assert [alert.code for alert in found] == ["BELOW_REORDER_POINT"]
        assert found[0].meta["on_hand"] == 500

    def test_exactly_at_the_point_is_silent(self, org):
        product = make_product(org, "Amoxicillin")
        product.reorder_point_base = 500
        product.save(update_fields=["reorder_point_base"])
        location = make_location(org, "Store", "MAIN")
        batch = make_batch(org, product, number="A1")
        inventory.post_movement(
            organization=org,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(5, uom(product, "PACK")),
        )
        assert inventory_checks.below_reorder_point(organization=org) == []

    def test_an_unset_point_is_not_a_threshold_of_zero(self, org):
        make_product(org, "Never reordered")
        assert inventory_checks.below_reorder_point(organization=org) == []


class TestFatigue:
    def test_a_screen_shows_at_most_three(self):
        found = [warning(f"W{n}") for n in range(7)]
        view = summarise(found)
        assert len(view["visible"]) == 3
        assert view["collapsed"] == 4

    def test_the_worst_are_the_ones_shown(self):
        found = [info("I"), warning("W"), critical("C")]
        view = summarise(found)
        assert [alert["code"] for alert in view["visible"]] == ["C", "W", "I"]

    def test_the_counts_are_reported(self):
        view = summarise([critical("C"), warning("W1"), warning("W2")])
        assert view["counts"]["CRITICAL"] == 1
        assert view["counts"]["WARNING"] == 2
        assert view["counts"]["INFO"] == 0
