"""Reading the alerts that apply right now.

A screen asks for its own set rather than the whole system's: an alert
the user cannot act on from where they are standing is noise, and noise
is what teaches people to click through the ones that matter.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog import checks as catalog_checks
from commerce import checks as commerce_checks
from core.alerts import seed_alert_rules, summarise
from core.models import AlertRule, ControlledQuota
from inventory import checks as inventory_checks

#: Which checks a screen runs. Named so the client asks for a scope
#: rather than enumerating check functions over the wire.
SCOPES = {
    "inventory": lambda org: [
        *inventory_checks.short_dated_batches(organization=org),
        *inventory_checks.below_reorder_point(organization=org),
    ],
    "receivables": lambda org: commerce_checks.receivables_overdue(supplier=org),
}


class AlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = request.user.organization
        if organization is None:
            return Response(summarise([]))

        scope = request.query_params.get("scope", "inventory")
        check = SCOPES.get(scope)
        if check is None:
            return Response(summarise([]))
        return Response(summarise(check(organization)))


class RegulatorExtractView(APIView):
    """Everything an inspection asks for, for a dated range.

    Read-only by construction — there is no write path here — and bounded
    by the range asked for, so two inspectors asking about two periods
    get two answers rather than one moving one.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import date

        from core import extracts
        from core.exceptions import DomainError

        try:
            start = date.fromisoformat(request.query_params["start"])
            end = date.fromisoformat(request.query_params["end"])
        except (KeyError, ValueError):
            raise DomainError(
                "Give a start and end date.", code="period_required"
            )
        if end < start:
            raise DomainError("The period ends before it starts.", code="invalid_period")

        return Response(
            extracts.bundle(
                organization=request.user.organization, start=start, end=end
            )
        )


class ProductAlertView(APIView):
    """Everything wrong with one product, for its detail modal."""

    permission_classes = [IsAuthenticated]

    def get(self, request, product_id):
        from catalog.models import Product

        product = Product.tenant_objects.filter(pk=product_id).first()
        if product is None:
            return Response(summarise([]))
        return Response(summarise(catalog_checks.registration(product=product)))


class AlertRuleSerializer(serializers.ModelSerializer):
    """A threshold, with the dates it applied between.

    Superseded rather than edited: an alert that fired in March must stay
    explainable under March's threshold, so changing one closes the
    current row and opens the next.
    """

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "code",
            "severity",
            "threshold",
            "is_active",
            "effective_from",
            "effective_to",
        ]


class AlertRuleViewSet(viewsets.ModelViewSet):
    """The numbers alerts fire at, editable by the organization.

    A threshold nobody can see is not configuration. Listing seeds the
    defaults on first read so the screen is never empty and the values in
    force are always visible as real dated rows.
    """

    serializer_class = AlertRuleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return AlertRule.tenant_objects.order_by("code", "-effective_from")

    def list(self, request, *args, **kwargs):
        if not self.get_queryset().exists():
            seed_alert_rules(request.user.organization)
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        instance.effective_to = timezone.localdate()
        instance.is_active = False
        instance.save(update_fields=["effective_to", "is_active", "modified_at"])


class ControlledQuotaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ControlledQuota
        fields = [
            "id",
            "schedule",
            "period",
            "limit_base",
            "authority_reference",
            "effective_from",
            "effective_to",
        ]


class ControlledQuotaViewSet(viewsets.ModelViewSet):
    """What the regulator caps, per schedule, per period."""

    serializer_class = ControlledQuotaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ControlledQuota.tenant_objects.order_by("schedule", "-effective_from")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        instance.effective_to = timezone.localdate()
        instance.save(update_fields=["effective_to", "modified_at"])
