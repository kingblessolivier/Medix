"""Reading the alerts that apply right now.

A screen asks for its own set rather than the whole system's: an alert
the user cannot act on from where they are standing is noise, and noise
is what teaches people to click through the ones that matter.
"""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog import checks as catalog_checks
from commerce import checks as commerce_checks
from core.alerts import summarise
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


class ProductAlertView(APIView):
    """Everything wrong with one product, for its detail modal."""

    permission_classes = [IsAuthenticated]

    def get(self, request, product_id):
        from catalog.models import Product

        product = Product.tenant_objects.filter(pk=product_id).first()
        if product is None:
            return Response(summarise([]))
        return Response(summarise(catalog_checks.registration(product=product)))
