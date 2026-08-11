"""Sales endpoints.

Views authorize, deserialize, call a service, serialize. Every gate lives
in `sales.services` — a rule enforced only in a view is bypassable by the
agent sync endpoint, a management command, or a Celery task.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, UnitOfMeasure
from core.exceptions import DomainError
from core.models import User
from fiscal.models import FiscalRecord
from fiscal.services import FiscalIntegrationService, exceptions_for
from inventory.models import Batch, Location
from sales import payments as payment_services
from sales import services, shifts as shift_services
from sales.models import (
    ControlledDeliveryEntry,
    Prescription,
    Sale,
    SaleStatus,
    Shift,
    ShiftStatus,
    Till,
)
from sales.serializers import (
    AddLineSerializer,
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


class PrescriptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PrescriptionSerializer

    def get_queryset(self):
        return Prescription.tenant_objects.select_related("patient", "prescriber").all()

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """A registered pharmacist confirms. OCR never does."""
        prescription = self.get_object()
        verified = services.verify_prescription(
            prescription=prescription, pharmacist=request.user
        )
        return Response(PrescriptionSerializer(verified).data)


class TillViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TillSerializer

    def get_queryset(self):
        return Till.tenant_objects.all()


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
