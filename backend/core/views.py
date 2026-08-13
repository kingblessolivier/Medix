from django.db import connection
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import onboarding, search as search_service
from core.capabilities import Capability, require_capability
from core.models import LicenceKind, PremisesLicence


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Liveness plus a database round trip."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return Response({"status": "ok", "database": "ok"})


@api_view(["GET"])
def me(request):
    """Who am I, and which organization am I acting for."""
    user = request.user
    org = user.organization
    return Response(
        {
            "id": str(user.id),
            "username": user.username,
            "name": user.get_full_name() or user.username,
            "organization": (
                {
                    "id": str(org.id),
                    "name": org.name,
                    "primary_kind": org.primary_kind,
                }
                if org
                else None
            ),
        }
    )


class RegisterPharmacySerializer(serializers.Serializer):
    """What a depot must know to admit a pharmacy.

    The licence fields are required because capability derives from the
    licence — a pharmacy registered without one can sign in and do
    nothing, and it is better to refuse than to create that.
    """

    name = serializers.CharField(max_length=200)
    licence_kind = serializers.ChoiceField(choices=LicenceKind.choices)
    licence_number = serializers.CharField(max_length=60)
    licence_expiry = serializers.DateField()
    licence_issued_on = serializers.DateField(required=False, allow_null=True)
    tin = serializers.CharField(max_length=20, required=False, allow_blank=True)

    branch_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    branch_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)

    admin_full_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    admin_email = serializers.EmailField(required=False, allow_blank=True)
    admin_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    pharmacist_council_number = serializers.CharField(
        max_length=60, required=False, allow_blank=True
    )
    pharmacist_expiry = serializers.DateField(required=False, allow_null=True)

    credit_limit = serializers.IntegerField(min_value=0, default=0)
    payment_terms_days = serializers.IntegerField(min_value=0, default=0)


class RegisterPharmacyView(APIView):
    """A depot admits a retail pharmacy onto the platform.

    Nobody signs themselves up — this is a closed distribution network,
    and admission is an act one organization performs on another. Only an
    organization that can publish listings may do it: admitting a
    customer you cannot supply is not a meaningful act.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        require_capability(request.user.organization, Capability.PUBLISH_LISTINGS)

        payload = RegisterPharmacySerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        result = onboarding.register_pharmacy(
            registered_by=request.user, **payload.validated_data
        )
        organization = result["organization"]
        administrator = result["administrator"]

        return Response(
            {
                "organization": {
                    "id": str(organization.id),
                    "name": organization.name,
                    "primary_kind": organization.primary_kind,
                    "tin": organization.tin,
                },
                "licence": {
                    "number": result["licence"].number,
                    "kind": result["licence"].kind,
                    "expiry": result["licence"].expiry,
                },
                "administrator": {
                    "username": administrator.username,
                    "email": administrator.email,
                },
                # Shown once, on this response. It is not stored readable
                # and cannot be retrieved again.
                "temporary_password": result["temporary_password"],
                "relationship": (
                    str(result["relationship"].id) if result["relationship"] else None
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class SearchView(APIView):
    """One question — where is that thing — across every record type.

    A pharmacist holding a carton with a number on it should not have to
    know which screen that number belongs to.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        found = search_service.search(
            user=request.user, term=request.query_params.get("q", "")
        )
        # Reaching a patient by search is an access to health data, and
        # docs/16 counts reads as events worth recording.
        if any(hit["kind"] == "patient" for hit in found["results"]):
            from core import audit

            audit.record(
                action="sales.patient.searched",
                actor=request.user,
                after={"term": found["term"]},
                organization=request.user.organization,
            )
        return Response(found)


class PharmacyViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Pharmacies this depot has registered, and their standing.

    Reads across the tenant boundary on purpose and narrowly: a depot is
    entitled to know which pharmacies it supplies, their licence state
    and what they owe — and nothing about their stock, their patients or
    their other suppliers.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = serializers.Serializer

    def list(self, request):
        from commerce import invoicing
        from commerce.models import TradingRelationship

        relationships = (
            TradingRelationship.objects.filter(organization=request.user.organization)
            .select_related("customer")
            .order_by("customer__name")
        )

        rows = []
        for relationship in relationships:
            customer = relationship.customer
            licence = (
                PremisesLicence.objects.filter(organization=customer)
                .order_by("-expiry")
                .first()
            )
            rows.append(
                {
                    "id": str(customer.id),
                    "name": customer.name,
                    "tin": customer.tin,
                    "primary_kind": customer.primary_kind,
                    "licence_number": licence.number if licence else "",
                    "licence_expiry": licence.expiry if licence else None,
                    "licence_valid": bool(licence and licence.is_valid),
                    "is_verified": relationship.is_verified,
                    "is_active": relationship.is_active,
                    "credit_limit": relationship.credit_limit,
                    "payment_terms_days": relationship.payment_terms_days,
                    "outstanding": invoicing.outstanding_for(
                        supplier=request.user.organization, customer=customer
                    ),
                }
            )
        return Response(rows)
