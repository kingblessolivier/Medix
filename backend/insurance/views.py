"""Insurance endpoints."""

from __future__ import annotations

from datetime import date

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import DomainError
from insurance import services
from insurance.models import (
    CapitationReceipt,
    Claim,
    CoverageRule,
    Member,
    Scheme,
    SchemeContract,
)
from insurance.serializers import (
    CapitationReceiptSerializer,
    ClaimPaymentRequestSerializer,
    ClaimResponseSerializer,
    ClaimSerializer,
    CoverageRuleSerializer,
    MemberSerializer,
    SchemeContractSerializer,
    SchemeSerializer,
)


class SchemeViewSet(viewsets.ModelViewSet):
    serializer_class = SchemeSerializer

    def get_queryset(self):
        return Scheme.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        """Deactivated. Claims point at it, and history must stay readable."""
        instance.is_active = False
        instance.save(update_fields=["is_active", "modified_at"])


class SchemeContractViewSet(viewsets.ModelViewSet):
    """Effective-dated. Superseded rather than edited."""

    serializer_class = SchemeContractSerializer

    def get_queryset(self):
        queryset = SchemeContract.tenant_objects.select_related("scheme")
        scheme = self.request.query_params.get("scheme")
        if scheme:
            queryset = queryset.filter(scheme_id=scheme)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class CoverageRuleViewSet(viewsets.ModelViewSet):
    serializer_class = CoverageRuleSerializer

    def get_queryset(self):
        queryset = CoverageRule.tenant_objects.select_related("product", "category")
        contract = self.request.query_params.get("contract")
        if contract:
            queryset = queryset.filter(contract_id=contract)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class MemberViewSet(viewsets.ModelViewSet):
    serializer_class = MemberSerializer

    def get_queryset(self):
        queryset = Member.tenant_objects.select_related("scheme", "patient")
        patient = self.request.query_params.get("patient")
        if patient:
            queryset = queryset.filter(patient_id=patient)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class EligibilityView(APIView):
    """Can this patient use cover here, today.

    Answers with a reason rather than a boolean: "not a member", "card
    expired" and "we are not on their panel" are three different
    conversations at the counter.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from sales.models import Patient

        patient = get_object_or_404(
            Patient.tenant_objects, pk=request.query_params.get("patient")
        )
        scheme = None
        if request.query_params.get("scheme"):
            scheme = get_object_or_404(
                Scheme.tenant_objects, pk=request.query_params["scheme"]
            )

        found = services.check_eligibility(
            organization=request.user.organization, patient=patient, scheme=scheme
        )
        return Response(found.as_dict())


class SaleCoverView(APIView):
    """What the patient pays and what the scheme is asked for.

    Read before completing, so the counter can take the co-pay rather
    than the full amount.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, sale_id):
        from sales.models import Sale

        sale = get_object_or_404(
            Sale.tenant_objects.prefetch_related("lines__product"), pk=sale_id
        )
        patient = sale.prescription.patient if sale.prescription_id else sale.patient
        eligibility = services.check_eligibility(
            organization=request.user.organization, patient=patient
        )
        priced = services.price_sale(
            organization=request.user.organization, sale=sale, eligibility=eligibility
        )

        return Response(
            {
                "covered": priced["covered"],
                "reason": priced["reason"],
                "model": priced.get("model", ""),
                "gross": priced["gross"],
                "scheme_amount": priced["scheme_amount"],
                "patient_amount": priced["patient_amount"],
                "eligibility": eligibility.as_dict(),
                "lines": [
                    {
                        "sale_line": str(entry["line"].id),
                        "product": entry["line"].product.name,
                        "gross": entry["split"].gross,
                        "covered": entry["split"].covered,
                        "patient": entry["split"].patient,
                        "coverage_basis_points": entry["split"].basis_points,
                        "note": entry["split"].note,
                    }
                    for entry in priced["lines"]
                ],
            }
        )


class ClaimViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Claims are raised by dispensing, not created by hand.

    A claim nobody dispensed against is a claim with no evidence, so
    there is no create endpoint — `raise_claim` runs when a covered sale
    completes.
    """

    serializer_class = ClaimSerializer

    def get_queryset(self):
        queryset = Claim.tenant_objects.select_related(
            "scheme", "member__patient", "sale", "contract"
        ).prefetch_related("lines__sale_line__product", "payments")
        state = self.request.query_params.get("status")
        scheme = self.request.query_params.get("scheme")
        if state:
            queryset = queryset.filter(status=state)
        if scheme:
            queryset = queryset.filter(scheme_id=scheme)
        return queryset

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        claim = services.submit_claim(claim=self.get_object(), performed_by=request.user)
        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        payload = ClaimResponseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        claim = services.record_response(
            claim=self.get_object(),
            performed_by=request.user,
            allowed=data.get("allowed"),
            rejections=data.get("rejections"),
            reason=data.get("reason", ""),
            scheme_reference=data.get("scheme_reference", ""),
        )
        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=["post"], url_path="payments")
    def record_payment(self, request, pk=None):
        payload = ClaimPaymentRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        services.record_claim_payment(
            claim=self.get_object(),
            amount=data["amount"],
            performed_by=request.user,
            received_on=data.get("received_on"),
            remittance_reference=data.get("remittance_reference", ""),
        )
        claim = self.get_object()
        claim.refresh_from_db()
        return Response(ClaimSerializer(claim).data)

    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        claim = services.write_off_claim(
            claim=self.get_object(),
            performed_by=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(ClaimSerializer(claim).data)


class CapitationReceiptViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = CapitationReceiptSerializer

    def get_queryset(self):
        return CapitationReceipt.tenant_objects.select_related("contract__scheme")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class SchemeReceivablesView(APIView):
    """What every scheme owes, aged — and what was refused.

    Rejections are counted apart from the buckets: they are not late,
    they are refused, and mixing them makes the ageing look worse while
    hiding work that could still recover the money.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            services.receivables_by_scheme(organization=request.user.organization)
        )


class CapitationUtilisationView(APIView):
    """Dispensed against paid, for a capitation contract."""

    permission_classes = [IsAuthenticated]

    def get(self, request, contract_id):
        contract = get_object_or_404(SchemeContract.tenant_objects, pk=contract_id)

        def parse(name, fallback):
            raw = request.query_params.get(name)
            return date.fromisoformat(raw) if raw else fallback

        from django.utils import timezone

        end = parse("end", timezone.localdate())
        start = parse("start", end.replace(day=1))
        if end < start:
            raise DomainError("The period ends before it starts.", code="invalid_period")

        return Response(
            services.capitation_utilisation(
                organization=request.user.organization,
                contract=contract,
                start=start,
                end=end,
            )
        )
