from django.db import connection
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.permissions import TenantScoped
from rest_framework.response import Response
from rest_framework.views import APIView

from core import onboarding, search as search_service
from core.capabilities import Capability, require_capability
from core.models import (
    Branch,
    LicenceKind,
    LicenceStatus,
    PharmacistRegistration,
    PremisesLicence,
    User,
)


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

    permission_classes = [IsAuthenticated, TenantScoped]

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

    permission_classes = [IsAuthenticated, TenantScoped]

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

    permission_classes = [IsAuthenticated, TenantScoped]
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


# --------------------------------------------------------------------------
# Rate-limited authentication
# --------------------------------------------------------------------------


class AuthThrottle(ScopedRateThrottle):
    """10/min per IP — docs/07-api.md.

    Anonymous, so it keys on address rather than user: the whole point is
    the requests that never succeed in authenticating.
    """

    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [AuthThrottle]


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [AuthThrottle]


# --------------------------------------------------------------------------
# Licences and registrations
# --------------------------------------------------------------------------
#
# Writable, which the compliance dashboard was not. It could report that a
# premises licence expires in thirty days and offered no way to record the
# renewal — a screen that trains people to ignore it.
#
# The consequence was not cosmetic. Capability derives from held licences,
# so an expired one silently removes what the pharmacy may do; and
# verifying a prescription needs a current registration, so a pharmacy with
# no way to record one could never dispense a prescription-only medicine.


class PremisesLicenceSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    is_valid = serializers.SerializerMethodField()
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = PremisesLicence
        fields = [
            "id",
            "branch",
            "branch_name",
            "kind",
            "number",
            "issued_on",
            "expiry",
            "status",
            "issuing_authority",
            "is_valid",
            "days_to_expiry",
        ]

    def get_is_valid(self, licence) -> bool:
        return (
            licence.status == LicenceStatus.ACTIVE
            and licence.expiry >= timezone.localdate()
        )

    def get_days_to_expiry(self, licence) -> int:
        return (licence.expiry - timezone.localdate()).days


class PharmacistRegistrationSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    is_valid = serializers.BooleanField(read_only=True)
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = PharmacistRegistration
        fields = [
            "id",
            "user",
            "user_name",
            "council_number",
            "issued_on",
            "expiry",
            "status",
            "is_valid",
            "days_to_expiry",
        ]

    def get_user_name(self, registration) -> str:
        return registration.user.get_full_name() or registration.user.username

    def get_days_to_expiry(self, registration) -> int:
        return (registration.expiry - timezone.localdate()).days


class PremisesLicenceViewSet(viewsets.ModelViewSet):
    """Renewing is adding a row, never editing the old one.

    A licence is evidence of what was permitted between two dates.
    Editing last year's expiry into this year's would erase the fact that
    there was ever a gap — which is exactly what an inspection asks
    about. Nothing here prevents an edit, but the screen offers renewal
    as a new record.
    """

    serializer_class = PremisesLicenceSerializer
    permission_classes = [IsAuthenticated, TenantScoped]

    def get_queryset(self):
        return PremisesLicence.tenant_objects.select_related("branch").order_by("-expiry")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class PharmacistRegistrationViewSet(viewsets.ModelViewSet):
    """Who may dispense, and until when."""

    serializer_class = PharmacistRegistrationSerializer
    permission_classes = [IsAuthenticated, TenantScoped]

    def get_queryset(self):
        return PharmacistRegistration.tenant_objects.select_related("user").order_by(
            "-expiry"
        )

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class ColleagueSerializer(serializers.ModelSerializer):
    """Who a registration can be attached to."""

    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "name"]

    def get_name(self, user) -> str:
        return user.get_full_name() or user.username


class ColleagueViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ColleagueSerializer
    permission_classes = [IsAuthenticated, TenantScoped]

    def get_queryset(self):
        return User.objects.filter(
            organization=self.request.user.organization, is_active=True
        ).order_by("username")


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "code", "is_active"]


class BranchViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The premises this organization operates from.

    Read-only and needed because a licence is issued to a branch, not to
    an organization — there was no way to name one from the client.
    """

    serializer_class = BranchSerializer
    permission_classes = [IsAuthenticated, TenantScoped]

    def get_queryset(self):
        return Branch.tenant_objects.order_by("code")
