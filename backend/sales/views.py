"""Sales endpoints.

Views authorize, deserialize, call a service, serialize. Every gate lives
in `sales.services` — a rule enforced only in a view is bypassable by the
agent sync endpoint, a management command, or a Celery task.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from core.permissions import TenantScoped
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, UnitOfMeasure
from core import audit
from core.exceptions import DomainError
from core.models import User
from core.quantity import Quantity
from fiscal.models import FiscalRecord
from fiscal.services import FiscalIntegrationService, exceptions_for
from inventory.models import Batch, Location
from sales import payments as payment_services
from core.alerts import summarise
from sales import services, shifts as shift_services
from sales.models import (
    ControlledDeliveryEntry,
    Patient,
    PatientAllergy,
    Prescriber,
    Prescription,
    Sale,
    SaleLine,
    TaxRule,
    SaleStatus,
    Shift,
    ShiftStatus,
    Till,
)
from sales.serializers import (
    AddLineSerializer,
    CreatePrescriptionSerializer,
    PatientAllergySerializer,
    PatientSerializer,
    PrescriberSerializer,
    SaleReturnSerializer,
    TaxRuleSerializer,
    CloseShiftSerializer,
    CompleteSaleSerializer,
    ControlledEntrySerializer,
    DayEndSerializer,
    PrescriptionSerializer,
    SaleSerializer,
    ShiftSerializer,
    StartSaleSerializer,
    TakePaymentSerializer,
    TillSerializer,
)


class SaleViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = SaleSerializer

    def get_queryset(self):
        return (
            Sale.tenant_objects.select_related("branch", "location", "cashier")
            .prefetch_related("lines__product", "lines__batch", "lines__uom", "payments")
            .all()
        )

    def create(self, request):
        payload = StartSaleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        location = get_object_or_404(Location.tenant_objects, pk=payload.validated_data["location"])
        till = None
        if payload.validated_data.get("till"):
            till = get_object_or_404(Till.tenant_objects, pk=payload.validated_data["till"])

        shift = Shift.objects.filter(till=till, status=ShiftStatus.OPEN).first() if till else None

        # TenantModel uses related_name="+", so there is no branch_set to
        # fall back to. A location without a branch is a setup error and
        # should say so rather than fail deeper in.
        if location.branch is None:
            raise DomainError(
                f"{location.name} is not attached to a branch.",
                code="location_has_no_branch",
            )

        sale = services.start_sale(
            organization=request.user.organization,
            branch=location.branch,
            location=location,
            cashier=request.user,
            till=till,
            shift=shift,
        )
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        sale = self.get_object()
        payload = AddLineSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        product = get_object_or_404(Product.tenant_objects, pk=data["product"])
        try:
            uom = UnitOfMeasure.objects.get(product=product, code=data["uom_code"])
        except UnitOfMeasure.DoesNotExist:
            raise DomainError(
                f"{product.name} has no unit '{data['uom_code']}'.", code="unknown_uom"
            )

        batch = None
        if data.get("batch"):
            batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
            if not data.get("override_reason"):
                raise DomainError(
                    "A manual batch choice needs a reason.", code="override_reason_required"
                )

        services.add_line(
            sale=sale,
            product=product,
            quantity=data["quantity"],
            uom=uom,
            unit_price=data["unit_price"],
            discount=data.get("discount", 0),
            batch=batch,
        )
        sale.refresh_from_db()
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=["get"])
    def clinical(self, request, pk=None):
        """What the pharmacist must see before completing.

        Interaction state is reported separately from the alerts, and
        `NOT_AVAILABLE` is not the same answer as an empty list — one
        says nobody looked, the other says somebody looked and found
        nothing. The counter prints the difference.
        """
        from sales import clinical as clinical_checks
        from sales import interactions

        sale = self.get_object()
        lines = list(sale.lines.select_related("product__category"))
        products = [line.product for line in lines]
        patient = sale.prescription.patient if sale.prescription_id else sale.patient

        found = clinical_checks.for_dispensing(patient=patient, products=products)
        interaction = interactions.check(products=products)

        return Response(
            {
                **summarise(found + interaction.alerts),
                "patient_known": patient is not None,
                "interactions": interaction.as_dict(),
                "interaction_notice": (
                    "" if interaction.was_checked else interactions.NOT_CHECKED_NOTICE
                ),
            }
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        sale = self.get_object()
        payload = CompleteSaleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        pharmacist = None
        if data.get("pharmacist"):
            pharmacist = get_object_or_404(
                User.objects.filter(organization=request.user.organization),
                pk=data["pharmacist"],
            )
        prescription = None
        if data.get("prescription"):
            prescription = get_object_or_404(
                Prescription.tenant_objects, pk=data["prescription"]
            )

        completed = services.complete_sale(
            sale=sale,
            performed_by=request.user,
            acknowledged=data.get("acknowledged", []),
            clinical_reason=data.get("clinical_reason", ""),
            pharmacist=pharmacist,
            prescription=prescription,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        # Goods have left the counter, so the invoice is due now.
        FiscalIntegrationService().submit(completed)
        completed.refresh_from_db()
        return Response(SaleSerializer(completed).data)

    @action(detail=True, methods=["post"], url_path="payments")
    def take_payment(self, request, pk=None):
        sale = self.get_object()
        payload = TakePaymentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        payment_services.take_payment(
            sale=sale,
            method=data["method"],
            amount=data["amount"],
            performed_by=request.user,
            provider_code=data.get("provider_code", ""),
            phone=data.get("phone", ""),
        )
        sale.refresh_from_db()
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        sale = self.get_object()
        reason = request.data.get("reason", "")
        if not reason.strip():
            raise DomainError("A void needs a reason.", code="reason_required")
        if sale.status != SaleStatus.DRAFT:
            raise DomainError(
                "Only a draft can be voided. Post a credit note instead.",
                code="cannot_void",
            )
        sale.status = SaleStatus.VOIDED
        sale.reason = reason
        sale.modified_by = request.user
        sale.save(update_fields=["status", "reason", "modified_by", "modified_at"])
        return Response(SaleSerializer(sale).data)


class PrescriptionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PrescriptionSerializer

    def get_queryset(self):
        return Prescription.tenant_objects.select_related("patient", "prescriber").all()

    def create(self, request):
        """Raised at the counter, pending verification.

        Created PENDING and stays there: only a registered pharmacist
        moves it, through `verify`. OCR may fill the extract, and it
        never authorizes.
        """
        payload = CreatePrescriptionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        prescription = Prescription.objects.create(
            organization=request.user.organization,
            patient=get_object_or_404(Patient.tenant_objects, pk=data["patient"]),
            prescriber=(
                get_object_or_404(Prescriber.tenant_objects, pk=data["prescriber"])
                if data.get("prescriber")
                else None
            ),
            issued_on=data.get("issued_on"),
            number=data.get("number", ""),
            created_by=request.user,
        )
        audit.record(
            action="sales.prescription.raised",
            subject=prescription,
            actor=request.user,
            after={"patient": prescription.patient.full_name},
            organization=request.user.organization,
        )
        return Response(
            PrescriptionSerializer(prescription).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """A registered pharmacist confirms. OCR never does."""
        prescription = self.get_object()
        verified = services.verify_prescription(
            prescription=prescription, pharmacist=request.user
        )
        return Response(PrescriptionSerializer(verified).data)


class ShiftViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ShiftSerializer

    def get_queryset(self):
        return Shift.tenant_objects.select_related("till").all()

    def create(self, request):
        till = get_object_or_404(Till.tenant_objects, pk=request.data.get("till"))
        shift = shift_services.open_shift(
            till=till,
            opened_by=request.user,
            opening_float=int(request.data.get("opening_float", 0)),
        )
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="x-report")
    def x_report(self, request, pk=None):
        """Reads the shift without closing it."""
        summary = shift_services.report(self.get_object())
        return Response(DayEndSerializer(summary).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """The Z report. Closes the shift and records the variance."""
        payload = CloseShiftSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        summary = shift_services.close_shift(
            shift=self.get_object(),
            counted_cash=payload.validated_data["counted_cash"],
            closed_by=request.user,
            variance_reason=payload.validated_data.get("variance_reason", ""),
            allow_pending=payload.validated_data.get("allow_pending", False),
        )
        return Response(DayEndSerializer(summary).data)


class ControlledRegisterView(APIView):
    """The statutory register, as it is reported to an inspector."""

    def get(self, request):
        entries = ControlledDeliveryEntry.objects.filter(
            organization=request.user.organization
        ).select_related("dispensed_by").order_by("entered_at")
        return Response(ControlledEntrySerializer(entries, many=True).data)


class FiscalExceptionView(APIView):
    """Sales whose fiscal submission failed. A screen, not a log file."""

    def get(self, request):
        records = exceptions_for(request.user.organization)
        return Response(
            [
                {
                    "id": str(r.id),
                    "sale_number": r.sale.number,
                    "status": r.status,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                    "attempts": r.attempts,
                    "created_at": r.created_at,
                }
                for r in records
            ]
        )

    def post(self, request):
        record = get_object_or_404(
            FiscalRecord.objects.filter(organization=request.user.organization),
            pk=request.data.get("record"),
        )
        retried = FiscalIntegrationService().retry(record)
        return Response({"id": str(retried.id), "status": retried.status})


class PaymentCallbackView(APIView):
    """Inbound provider callback.

    Returns 200 quickly and is idempotent — a provider that retries must
    not double-count, and a slow response makes them retry more.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request, provider: str):
        from sales.models import Payment

        reference = request.data.get("reference", "")
        confirmed = bool(request.data.get("confirmed"))

        payment = Payment.objects.filter(provider_reference=reference).first()
        if payment is None:
            # Unknown reference: acknowledge so the provider stops retrying,
            # and leave it for reconciliation rather than erroring.
            return Response({"status": "unknown_reference"}, status=status.HTTP_200_OK)

        payment_services.resolve_payment(payment=payment, confirmed=confirmed)
        return Response({"status": "ok"})


class PatientViewSet(viewsets.ModelViewSet):
    """Sensitive personal data under Law 058/2021.

    Every read of a patient record is written to the audit stream, not
    just every write — `docs/16-security.md` treats access to health data
    as the event worth recording.
    """

    serializer_class = PatientSerializer
    filter_backends = [SearchFilter]
    search_fields = ["full_name", "phone", "national_id"]

    def get_queryset(self):
        return Patient.tenant_objects.prefetch_related("allergies")

    def perform_create(self, serializer):
        patient = serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )
        audit.record(
            action="sales.patient.created",
            subject=patient,
            actor=self.request.user,
            after={"full_name": patient.full_name},
            organization=self.request.user.organization,
        )

    def retrieve(self, request, *args, **kwargs):
        patient = self.get_object()
        audit.record(
            action="sales.patient.read",
            subject=patient,
            actor=request.user,
            organization=request.user.organization,
        )
        return Response(self.get_serializer(patient).data)


class PatientAllergyViewSet(viewsets.ModelViewSet):
    """Recorded by a pharmacist, against an ingredient."""

    serializer_class = PatientAllergySerializer

    def get_queryset(self):
        queryset = PatientAllergy.tenant_objects.select_related("patient")
        patient = self.request.query_params.get("patient")
        if patient:
            queryset = queryset.filter(patient_id=patient)
        return queryset

    def perform_create(self, serializer):
        allergy = serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )
        audit.record(
            action="sales.allergy.recorded",
            subject=allergy.patient,
            actor=self.request.user,
            after={"allergen": allergy.allergen, "severity": allergy.severity},
            organization=self.request.user.organization,
        )


class PrescriberViewSet(viewsets.ModelViewSet):
    serializer_class = PrescriberSerializer
    filter_backends = [SearchFilter]
    search_fields = ["full_name", "council_number", "facility"]

    def get_queryset(self):
        return Prescriber.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class TillViewSet(viewsets.ModelViewSet):
    serializer_class = TillSerializer

    def get_queryset(self):
        return Till.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class TaxRuleViewSet(viewsets.ModelViewSet):
    """Effective-dated. Superseded rather than edited.

    Deleting a rule a sale was priced under would make that sale
    unexplainable, so removal closes the rule with `effective_to`
    instead.
    """

    serializer_class = TaxRuleSerializer

    def get_queryset(self):
        return TaxRule.tenant_objects.order_by("treatment", "-effective_from")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        instance.effective_to = timezone.localdate()
        instance.save(update_fields=["effective_to", "modified_at"])


class SaleReturnView(APIView):
    """A customer bringing goods back.

    `restock` is the caller's decision, never a default — see
    `inventory.movements.sale_return`.
    """

    permission_classes = [IsAuthenticated, TenantScoped]

    def post(self, request):
        from inventory import movements

        payload = SaleReturnSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        line = get_object_or_404(
            SaleLine.objects.filter(sale__organization=request.user.organization),
            pk=data["sale_line"],
        )
        code = data.get("uom_code") or ""
        unit = (
            line.product.units.get(code=code) if code else line.product.base_uom
        )
        movements.sale_return(
            organization=request.user.organization,
            sale_line=line,
            quantity=Quantity(data["quantity"], unit),
            performed_by=request.user,
            reason=data["reason"],
            restock=data["restock"],
        )
        return Response({"returned": True}, status=status.HTTP_201_CREATED)
