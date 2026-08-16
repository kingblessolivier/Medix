"""Inventory endpoints.

The ledger is read-only over HTTP. Stock changes happen through actions
that call `services.post_movement()`; there is no way to POST a movement
directly, which is the point.
"""

from __future__ import annotations

from django_filters import rest_framework as filters
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated

from core.permissions import TenantScoped
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, UnitOfMeasure
from core.exceptions import DomainError
from core.pagination import LedgerCursorPagination
from core.quantity import Quantity
from inventory import counting, movements, services
from inventory.counting import StockCount
from inventory.models import (
    Batch,
    Location,
    MovementKind,
    StockBalance,
    StockMovement,
    StockStatus,
)
from inventory.serializers import (
    AdjustmentInputSerializer,
    AllocationPreviewInputSerializer,
    AllocationSerializer,
    BatchSerializer,
    LocationSerializer,
    ReceiptInputSerializer,
    StockBalanceSerializer,
    StockMovementSerializer,
    QuarantineSerializer,
    RecallSerializer,
    SupplierReturnSerializer,
    TransferSerializer,
)


def _resolve_uom(product: Product, code: str) -> UnitOfMeasure:
    try:
        return UnitOfMeasure.objects.get(product=product, code=code)
    except UnitOfMeasure.DoesNotExist:
        raise DomainError(
            f"{product.name} has no unit '{code}'.", code="unknown_uom"
        )


class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer

    def get_queryset(self):
        return Location.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class BatchFilter(filters.FilterSet):
    expiry_before = filters.DateFilter(field_name="expiry_date", lookup_expr="lte")
    expiry_after = filters.DateFilter(field_name="expiry_date", lookup_expr="gte")

    class Meta:
        model = Batch
        fields = ["product", "supplier", "cold_chain"]


class BatchViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BatchSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BatchFilter

    def get_queryset(self):
        return Batch.tenant_objects.select_related("product", "supplier").all()


class StockBalanceFilter(filters.FilterSet):
    expiry_before = filters.DateFilter(field_name="expiry_date", lookup_expr="lte")

    class Meta:
        model = StockBalance
        fields = ["product", "location", "batch", "status"]


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    """Current balances. Derived from the ledger, never edited directly."""

    serializer_class = StockBalanceSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = StockBalanceFilter

    def get_queryset(self):
        return (
            StockBalance.objects.filter(
                organization_id=self.request.organization_id, quantity_base__gt=0
            )
            .select_related("product", "batch", "location")
            .order_by("expiry_date")
        )

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Batches expiring inside a window, nearest first."""
        within = int(request.query_params.get("within_days", 90))
        rows = services.expiring_batches(
            organization=request.user.organization, within_days=within
        )
        page = self.paginate_queryset(rows)
        serializer = StockBalanceSerializer(page or rows, many=True)
        return self.get_paginated_response(serializer.data) if page else Response(serializer.data)


class MovementFilter(filters.FilterSet):
    occurred_after = filters.DateTimeFilter(field_name="occurred_at", lookup_expr="gte")
    occurred_before = filters.DateTimeFilter(field_name="occurred_at", lookup_expr="lte")

    class Meta:
        model = StockMovement
        fields = ["product", "batch", "location", "kind", "status"]


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """The ledger, readable. No create, no update, no delete.

    Cursor-paginated: rows insert constantly and OFFSET is expensive at
    depth.
    """

    serializer_class = StockMovementSerializer
    pagination_class = LedgerCursorPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = MovementFilter

    def get_queryset(self):
        return StockMovement.objects.filter(
            organization_id=self.request.organization_id
        ).select_related("product", "batch", "location", "performed_by")


class AllocationPreviewView(APIView):
    """Which batches would FEFO pick? Does not commit.

    The POS calls this to show the batch on a line before completion.
    """

    def post(self, request):
        payload = AllocationPreviewInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        product = get_object_or_404(Product.tenant_objects, pk=data["product"])
        location = get_object_or_404(Location.tenant_objects, pk=data["location"])
        uom = _resolve_uom(product, data["uom_code"])

        allocations = services.allocate_fefo(
            organization=request.user.organization,
            product=product,
            location=location,
            quantity=Quantity(data["quantity"], uom),
        )
        return Response(
            AllocationSerializer(
                [
                    {
                        "batch_id": a.batch.id,
                        "batch_number": a.batch.batch_number,
                        "expiry_date": a.batch.expiry_date,
                        "quantity_base": a.quantity_base,
                    }
                    for a in allocations
                ],
                many=True,
            ).data
        )


class ReceiveStockView(APIView):
    """Receive against a batch. Requires an Idempotency-Key."""

    def post(self, request):
        payload = ReceiptInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        location = get_object_or_404(Location.tenant_objects, pk=data["location"])
        uom = _resolve_uom(batch.product, data["uom_code"])

        result = services.post_movement(
            organization=request.user.organization,
            location=location,
            batch=batch,
            kind=MovementKind.PURCHASE_RECEIPT,
            quantity=Quantity(data["quantity"], uom),
            performed_by=request.user,
            reference=data.get("reference", ""),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return Response(
            StockMovementSerializer(result.movement).data,
            status=status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED,
        )


class AdjustStockView(APIView):
    """Adjust a balance. A reason is mandatory and is recorded."""

    def post(self, request):
        payload = AdjustmentInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        location = get_object_or_404(Location.tenant_objects, pk=data["location"])
        uom = _resolve_uom(batch.product, data["uom_code"])

        result = services.post_movement(
            organization=request.user.organization,
            location=location,
            batch=batch,
            kind=MovementKind.ADJUSTMENT,
            quantity=Quantity(data["quantity"], uom),
            performed_by=request.user,
            reason=data["reason"],
            status=StockStatus.AVAILABLE,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return Response(
            StockMovementSerializer(result.movement).data,
            status=status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED,
        )


def _uom_for(batch, code: str):
    """The unit a quantity was entered in, defaulting to the base."""
    if not code:
        return batch.product.base_uom
    from catalog.models import UnitOfMeasure

    try:
        return UnitOfMeasure.objects.get(product=batch.product, code=code)
    except UnitOfMeasure.DoesNotExist:
        raise DomainError(
            f"{batch.product.name} has no unit '{code}'.", code="unknown_uom"
        )


class StockActionView(APIView):
    """The ledger movements that had a kind and no way to happen.

    Transfer, quarantine, supplier return and recall. Each one posts
    through `inventory.services.post_movement` — there is no path here
    that writes a balance.
    """

    permission_classes = [IsAuthenticated, TenantScoped]

    def post(self, request, action: str):
        handlers = {
            "transfer": self._transfer,
            "quarantine": self._quarantine,
            "supplier-return": self._supplier_return,
            "recall": self._recall,
        }
        handler = handlers.get(action)
        if handler is None:
            raise DomainError(f"Unknown stock action '{action}'.", code="unknown_action")
        return handler(request)

    def _transfer(self, request):
        payload = TransferSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        result = movements.transfer(
            organization=request.user.organization,
            batch=batch,
            from_location=get_object_or_404(
                Location.tenant_objects, pk=data["from_location"]
            ),
            to_location=get_object_or_404(
                Location.tenant_objects, pk=data["to_location"]
            ),
            quantity=Quantity(
                data["quantity"], _uom_for(batch, data.get("uom_code", ""))
            ),
            performed_by=request.user,
            reason=data.get("reason", ""),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return Response({"reference": result["reference"]}, status=status.HTTP_201_CREATED)

    def _quarantine(self, request):
        payload = QuarantineSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        movements.quarantine(
            organization=request.user.organization,
            batch=batch,
            location=get_object_or_404(Location.tenant_objects, pk=data["location"]),
            quantity=Quantity(
                data["quantity"], _uom_for(batch, data.get("uom_code", ""))
            ),
            performed_by=request.user,
            reason=data["reason"],
        )
        return Response({"quarantined": True}, status=status.HTTP_201_CREATED)

    def _supplier_return(self, request):
        payload = SupplierReturnSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        batch = get_object_or_404(Batch.tenant_objects, pk=data["batch"])
        movements.supplier_return(
            organization=request.user.organization,
            batch=batch,
            location=get_object_or_404(Location.tenant_objects, pk=data["location"]),
            quantity=Quantity(
                data["quantity"], _uom_for(batch, data.get("uom_code", ""))
            ),
            performed_by=request.user,
            reason=data["reason"],
            status=data.get("status") or StockStatus.AVAILABLE,
        )
        return Response({"returned": True}, status=status.HTTP_201_CREATED)

    def _recall(self, request):
        payload = RecallSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        result = movements.recall(
            organization=request.user.organization,
            batch=get_object_or_404(Batch.tenant_objects, pk=data["batch"]),
            performed_by=request.user,
            reason=data["reason"],
            authority_reference=data.get("authority_reference", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class BatchTraceView(APIView):
    """Everywhere a batch went — the question a recall actually asks.

    Readable before a recall as well as after: deciding whether to recall
    means knowing how far the batch travelled.
    """

    permission_classes = [IsAuthenticated, TenantScoped]

    def get(self, request, batch_id):
        return Response(
            movements.trace_batch(
                organization=request.user.organization,
                batch=get_object_or_404(Batch.tenant_objects, pk=batch_id),
            )
        )


class OpeningBalanceView(APIView):
    """What is already on the shelves, on the day a pharmacy starts.

    Separate from receiving on purpose. Recording go-live stock as a
    purchase says the pharmacy bought two thousand boxes on its first
    day: it inflates that period's purchases, invents a supplier
    relationship, and makes the first month's margin meaningless.
    """

    def post(self, request):
        from inventory import golive

        location = get_object_or_404(
            Location.tenant_objects, pk=request.data.get("location")
        )
        loaded = golive.load_opening_balances(
            organization=request.user.organization,
            location=location,
            rows=request.data.get("rows") or [],
            performed_by=request.user,
            counted_on=request.data.get("counted_on") or None,
        )
        return Response(loaded.as_dict(), status=status.HTTP_201_CREATED)


class StockCountSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    counted_by_name = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()
    variance_base = serializers.SerializerMethodField()

    class Meta:
        model = StockCount
        fields = [
            "id",
            "reference",
            "location",
            "location_name",
            "status",
            "counted_by",
            "counted_by_name",
            "submitted_at",
            "approved_at",
            "note",
            "variance_base",
            "lines",
        ]

    def get_counted_by_name(self, count) -> str:
        user = count.counted_by
        return (user.get_full_name() or user.username) if user else ""

    def get_variance_base(self, count) -> int:
        return sum(line.variance_base for line in count.lines.all())

    def get_lines(self, count) -> list[dict]:
        return [
            {
                "id": str(line.id),
                "batch": str(line.batch_id),
                "batch_number": line.batch.batch_number,
                "product_name": line.batch.product.name,
                "expected_base": line.expected_base,
                "counted_base": line.counted_base,
                "variance_base": line.variance_base,
                "variance_value": line.variance_value,
                "reason": line.reason,
                "needs_a_reason": line.needs_a_reason,
            }
            for line in count.lines.select_related("batch__product")
        ]


class StockCountViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Counting a room, and the adjustment that follows.

    The count is not the correction. A counter writes down what is there;
    somebody who can authorise it approves, and only then does the ledger
    move — which is the control a stock take exists to provide.
    """

    serializer_class = StockCountSerializer

    def get_queryset(self):
        queryset = StockCount.tenant_objects.select_related("location", "counted_by")
        status_filter = self.request.query_params.get("status")
        return queryset.filter(status=status_filter) if status_filter else queryset

    def create(self, request):
        location = get_object_or_404(
            Location.tenant_objects, pk=request.data.get("location")
        )
        count = counting.open_count(
            organization=request.user.organization,
            location=location,
            performed_by=request.user,
        )
        return Response(
            StockCountSerializer(count).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        count = self.get_object()
        batch = get_object_or_404(Batch.tenant_objects, pk=request.data.get("batch"))
        counting.record_count(
            count=count,
            batch=batch,
            counted_base=int(request.data.get("counted_base", 0)),
            reason=request.data.get("reason", ""),
        )
        count.refresh_from_db()
        return Response(StockCountSerializer(count).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        count = counting.submit_count(count=self.get_object(), performed_by=request.user)
        return Response(StockCountSerializer(count).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        result = counting.approve_count(
            count=self.get_object(), performed_by=request.user
        )
        return Response(result)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        count = counting.cancel_count(
            count=self.get_object(),
            performed_by=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(StockCountSerializer(count).data)
