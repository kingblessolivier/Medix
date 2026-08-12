"""Finance endpoints."""

from __future__ import annotations

from rest_framework import mixins, status, viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.quantity import Quantity
from finance import reports, services
from finance.models import Expense, ExpenseCategory, WriteOff
from finance.serializers import (
    ExpenseCategorySerializer,
    ExpenseSerializer,
    PeriodQuerySerializer,
    WriteOffRequestSerializer,
    WriteOffSerializer,
)
from inventory.models import Batch, Location


class ExpenseCategoryViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    serializer_class = ExpenseCategorySerializer

    def get_queryset(self):
        return ExpenseCategory.tenant_objects.filter(is_active=True)

    def list(self, request, *args, **kwargs):
        # Seed on first read rather than at signup: an organization that
        # never opens the expense screen does not need the rows, and one
        # that does must not meet an empty list.
        if not self.get_queryset().exists():
            services.seed_categories(request.user.organization)
        return super().list(request, *args, **kwargs)


class ExpenseViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        queryset = Expense.tenant_objects.select_related("category")
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            queryset = queryset.filter(incurred_on__gte=start)
        if end:
            queryset = queryset.filter(incurred_on__lte=end)
        return queryset

    def create(self, request):
        payload = ExpenseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        expense = services.record_expense(
            organization=request.user.organization,
            category=get_object_or_404(
                ExpenseCategory.tenant_objects, pk=data["category"].id
            ),
            amount=data["amount"],
            incurred_on=data.get("incurred_on"),
            performed_by=request.user,
            description=data.get("description", ""),
            payee=data.get("payee", ""),
            reference=data.get("reference", ""),
            branch=data.get("branch"),
        )
        return Response(ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED)


class WriteOffViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = WriteOffSerializer

    def get_queryset(self):
        return WriteOff.tenant_objects.select_related("batch__product", "location")

    def create(self, request):
        payload = WriteOffRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        location = get_object_or_404(Location.tenant_objects, pk=data["location"])
        code = data.get("uom_code") or ""
        uom = (
            batch.product.units.get(code=code) if code else batch.product.base_uom
        )

        record = services.write_off(
            organization=request.user.organization,
            batch=batch,
            location=location,
            quantity=Quantity(data["quantity"], uom),
            reason=data["reason"],
            performed_by=request.user,
            witness_name=data.get("witness_name", ""),
            witness_role=data.get("witness_role", ""),
            written_off_on=data.get("written_off_on"),
        )
        return Response(WriteOffSerializer(record).data, status=status.HTTP_201_CREATED)


class PeriodReportView(APIView):
    """Invested against gained, for any range.

    Computed on request rather than read from a period table — which is
    what lets an arbitrary range be asked for at all, and what makes a
    backdated credit note correct history instead of leaving a stale
    total behind. See docs/28 §12.1.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = PeriodQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        report = reports.period_report(
            organization=request.user.organization,
            start=data["start"],
            end=data["end"],
            tier=data["tier"],
        )
        return Response(report.as_dict())


class ReceivablesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            reports.receivables_ageing(supplier=request.user.organization)
        )
