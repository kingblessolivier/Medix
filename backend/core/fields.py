"""Serializer fields for the value objects.

Money and Quantity are always objects on the wire, never bare numbers.
A client must never have to guess whether 28000 is francs or centimes,
or whether 6 means capsules or packs.

See docs/07-api.md.
"""

from __future__ import annotations

from rest_framework import serializers

from core.money import MINOR_UNITS, Money


class MoneyField(serializers.Field):
    """{"amount": 2800000, "currency": "RWF", "display": "RWF 28,000"}

    ``amount`` is minor units. Clients must not do arithmetic on
    ``display``.
    """

    def __init__(self, *args, currency_field: str = "cost_currency", **kwargs):
        self.currency_field = currency_field
        kwargs.setdefault("read_only", True)
        super().__init__(*args, **kwargs)

    def to_representation(self, value) -> dict:
        if isinstance(value, Money):
            money = value
        else:
            currency = getattr(self.parent.instance, self.currency_field, "RWF")
            money = Money(int(value), currency)
        return {
            "amount": money.amount,
            "currency": money.currency,
            "display": str(money),
        }

    def to_internal_value(self, data) -> Money:
        if not isinstance(data, dict) or "amount" not in data:
            raise serializers.ValidationError("Expected {amount, currency}.")
        currency = data.get("currency", "RWF")
        if currency not in MINOR_UNITS:
            raise serializers.ValidationError(f"Unknown currency {currency}.")
        try:
            amount = int(data["amount"])
        except (TypeError, ValueError):
            raise serializers.ValidationError("amount must be an integer in minor units.")
        return Money(amount, currency)


class QuantityField(serializers.Field):
    """{"value": 6, "uom": {"code": "UNIT", "name": "Capsule"}, "base_value": 6}

    ``base_value`` is included on responses so a client never converts.
    Requests may send ``value`` plus ``uom`` and the server converts.
    """

    def __init__(self, *args, uom_source: str | None = None, **kwargs):
        self.uom_source = uom_source
        kwargs.setdefault("read_only", True)
        super().__init__(*args, **kwargs)

    def to_representation(self, value) -> dict:
        # value is a base-unit integer; the UoM comes from the instance.
        instance = self.parent.instance
        uom = None
        if self.uom_source and instance is not None:
            uom = getattr(instance, self.uom_source, None)
        return {
            "value": int(value),
            "uom": {"code": uom.code, "name": uom.name} if uom else None,
            "base_value": int(value),
        }


class QuantityInput(serializers.Serializer):
    """Inbound quantity: a value plus the UoM code it is expressed in."""

    value = serializers.IntegerField()
    uom_code = serializers.CharField(max_length=20)

    def validate_value(self, value: int) -> int:
        if value == 0:
            raise serializers.ValidationError("Quantity cannot be zero.")
        return value
