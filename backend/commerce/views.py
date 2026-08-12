"""Commerce endpoints: marketplace, orders, receiving.

Marketplace browse is deliberately **not** tenant-scoped: the point is to
see what other pharmacies offer. It is a public catalogue of listings,
and every other resource here stays scoped as usual.
"""

from __future__ import annotations

from django.db.models import BigIntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog import services as catalog_services
from catalog.models import Product, UnitOfMeasure
from commerce import services
from commerce.models import (
    Availability,
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    TradingRelationship,
    VendorListing,
)
from commerce.serializers import (
    AddOrderLineSerializer,
    AddReceiptLineSerializer,
    DispatchSerializer,
    GoodsReceiptSerializer,
    MarketplaceRowSerializer,
    PublishListingSerializer,
    PurchaseOrderSerializer,
    ShipmentSerializer,
    StartOrderSerializer,
    StartReceiptSerializer,
    TradingRelationshipSerializer,
    VendorComparisonSerializer,
)
from core.exceptions import DomainError
from core.models import Organization
from inventory import services as inventory
from inventory.models import Batch, Location, StockBalance, StockStatus


def _resolve_uom(product: Product, code: str) -> UnitOfMeasure:
    try:
        return UnitOfMeasure.objects.get(product=product, code=code)
    except UnitOfMeasure.DoesNotExist:
        raise DomainError(f"{product.name} has no unit '{code}'.", code="unknown_uom")


class MarketplaceFilter(filters.FilterSet):
    product_type = filters.CharFilter(field_name="product__product_type__code")
    category = filters.CharFilter(field_name="product__category__name")
    legal_status = filters.CharFilter(field_name="product__legal_status")
    cold_chain = filters.BooleanFilter(field_name="product__cold_chain")
    vendor = filters.UUIDFilter(field_name="organization_id")
    max_price = filters.NumberFilter(field_name="price", lookup_expr="lte")

    class Meta:
        model = VendorListing
        fields = ["availability"]


class MarketplaceViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse what every vendor offers.

    Not tenant-scoped by design — a retail pharmacy must see other
    organizations' listings, which is the entire point of a marketplace.
    Only the listing itself is exposed; a vendor's stock and cost stay
    private.
    """

    serializer_class = MarketplaceRowSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = MarketplaceFilter

    def get_queryset(self):
        qs = (
            VendorListing.objects.filter(is_active=True)
            .select_related(
                "organization",
                "product",
                "product__product_type",
                "product__category",
                "product__registration",
                "price_uom",
            )
            .prefetch_related("product__units")
            .annotate(
                # A buyer comparing vendors needs to know whether the
                # seller actually holds the goods, and how long that stock
                # has left. Annotated rather than computed per row so the
                # list stays one query.
                stock_base=Coalesce(
                    Subquery(
                        StockBalance.objects.filter(
                            organization_id=OuterRef("organization_id"),
                            product_id=OuterRef("product_id"),
                            status=StockStatus.AVAILABLE,
                        )
                        .values("product_id")
                        .annotate(total=Sum("quantity_base"))
                        .values("total")[:1],
                        output_field=BigIntegerField(),
                    ),
                    Value(0),
                ),
                earliest_expiry=Subquery(
                    Batch.objects.filter(
                        organization_id=OuterRef("organization_id"),
                        product_id=OuterRef("product_id"),
                        expiry_date__gt=timezone.localdate(),
                    )
                    .order_by("expiry_date")
                    .values("expiry_date")[:1]
                ),
            )
            .order_by("product__name", "price")
        )
        # A pharmacy does not need its own listings back when shopping.
        exclude_own = self.request.query_params.get("exclude_own") == "true"
        if exclude_own and self.request.user.organization_id:
            qs = qs.exclude(organization_id=self.request.user.organization_id)

        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(product__name__icontains=search)
        return qs

    @action(detail=False, methods=["get"], url_path="compare")
    def compare(self, request):
        """Every vendor offering one product, cheapest first."""
        product = get_object_or_404(Product.objects.all(), pk=request.query_params.get("product"))
        rows = services.compare_vendors(product=product)
        return Response(
            VendorComparisonSerializer(
                [
                    {
                        "listing_id": r["listing"].id,
                        "vendor_name": r["vendor"].name,
                        "price": r["price"],
                        "uom": r["uom"],
                        "availability": r["availability"],
                        "stock_base": r["stock_base"],
                        "earliest_expiry": r["earliest_expiry"],
                        "moq": r["moq"],
                        "lead_time_days": r["lead_time_days"],
                    }
                    for r in rows
                ],
                many=True,
            ).data
        )


class ListingViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """A vendor's own listings. Publishing needs a wholesale licence."""

    serializer_class = MarketplaceRowSerializer

    def get_queryset(self):
        return VendorListing.tenant_objects.select_related("product", "price_uom").all()

    def create(self, request):
        payload = PublishListingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        product = get_object_or_404(Product.tenant_objects, pk=data["product"])
        listing = services.publish_listing(
            organization=request.user.organization,
            product=product,
            price=data["price"],
            price_uom=_resolve_uom(product, data["uom_code"]),
            availability=data.get("availability", Availability.AVAILABLE_NOW),
            moq=data.get("moq", 1),
            lead_time_days=data.get("lead_time_days", 1),
            performed_by=request.user,
        )
        return Response(
            MarketplaceRowSerializer(listing).data, status=status.HTTP_201_CREATED
        )


class PurchaseOrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PurchaseOrderSerializer

    def get_queryset(self):
        """Both sides see the order.

        The buyer finds it under Orders, the supplier in their fulfilment
        queue. Cross-organization visibility is modelled explicitly rather
        than by relaxing the tenant filter.
        """
        org_id = self.request.user.organization_id
        return (
            PurchaseOrder.objects.filter(
                models_or(org_id)
            )
            .select_related("supplier", "organization", "deliver_to")
            .prefetch_related("lines__product", "lines__uom")
            .distinct()
        )

    def create(self, request):
        payload = StartOrderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        supplier = get_object_or_404(Organization.objects.all(), pk=data["supplier"])
        deliver_to = get_object_or_404(Location.tenant_objects, pk=data["deliver_to"])

        order = services.start_order(
            organization=request.user.organization,
            supplier=supplier,
            deliver_to=deliver_to,
            performed_by=request.user,
            required_by=data.get("required_by"),
        )
        return Response(PurchaseOrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def draft(self, request):
        """The open draft for a supplier, opened if there isn't one.

        Backs 'Add to order' in the marketplace: the buyer picks products
        across several visits to the catalogue and they land on one order.
        """
        payload = StartOrderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        supplier = get_object_or_404(Organization.objects.all(), pk=data["supplier"])
        deliver_to = get_object_or_404(Location.tenant_objects, pk=data["deliver_to"])

        order = services.open_draft(
            organization=request.user.organization,
            supplier=supplier,
            deliver_to=deliver_to,
            performed_by=request.user,
        )
        return Response(PurchaseOrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        order = self.get_object()
        payload = AddOrderLineSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        listing = get_object_or_404(
            VendorListing.objects.select_related("product", "price_uom"),
            pk=payload.validated_data["listing"],
        )
        services.add_order_line(
            order=order, listing=listing, quantity=payload.validated_data["quantity"]
        )
        order.refresh_from_db()
        return Response(PurchaseOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        order = services.submit_order(order=self.get_object(), performed_by=request.user)
        return Response(PurchaseOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Supplier accepts. Only the supplier may."""
        order = services.confirm_order(order=self.get_object(), performed_by=request.user)
        return Response(PurchaseOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def dispatch_order(self, request, pk=None):
        """Ship what is outstanding. Only the supplier may.

        Named `dispatch_order` rather than `dispatch` because `dispatch`
        is View.dispatch — overriding it would break request routing.
        """
        payload = DispatchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        from_location = get_object_or_404(
            Location.objects.filter(organization=request.user.organization),
            pk=payload.validated_data["from_location"],
        )
        shipment = services.dispatch_order(
            order=self.get_object(),
            from_location=from_location,
            performed_by=request.user,
            carrier=payload.validated_data.get("carrier", ""),
        )
        return Response(ShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="shipments")
    def shipments(self, request, pk=None):
        """Delivery notes against this order — the buyer reads these too."""
        order = self.get_object()
        return Response(
            ShipmentSerializer(
                order.shipments.prefetch_related("lines__product", "lines__uom").all(),
                many=True,
            ).data
        )

    @action(detail=False, methods=["get"], url_path="fulfilment")
    def fulfilment(self, request):
        """The supplier's queue: orders placed with this organization."""
        orders = (
            PurchaseOrder.objects.filter(supplier=request.user.organization)
            .exclude(status="DRAFT")
            .select_related("organization", "deliver_to")
            .prefetch_related("lines__product")
            .order_by("-submitted_at")
        )
        page = self.paginate_queryset(orders)
        serializer = PurchaseOrderSerializer(page if page is not None else orders, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )


def models_or(org_id):
    """Buyer or supplier — both parties see the order."""
    from django.db.models import Q

    return Q(organization_id=org_id) | Q(supplier_id=org_id)


class GoodsReceiptViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = GoodsReceiptSerializer

    def get_queryset(self):
        return (
            GoodsReceipt.tenant_objects.select_related("supplier", "location", "order")
            .prefetch_related("lines__product", "lines__uom")
            .all()
        )

    def create(self, request):
        payload = StartReceiptSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        location = get_object_or_404(Location.tenant_objects, pk=data["location"])
        order = (
            get_object_or_404(PurchaseOrder.tenant_objects, pk=data["order"])
            if data.get("order")
            else None
        )
        supplier = (
            get_object_or_404(Organization.objects.all(), pk=data["supplier"])
            if data.get("supplier")
            else None
        )

        receipt = services.start_receipt(
            organization=request.user.organization,
            location=location,
            order=order,
            supplier=supplier,
            performed_by=request.user,
        )
        return Response(GoodsReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        receipt = self.get_object()
        payload = AddReceiptLineSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        order_line = (
            get_object_or_404(PurchaseOrderLine.objects.all(), pk=data["order_line"])
            if data.get("order_line")
            else None
        )

        # An order line points at the *supplier's* catalog row, so the id
        # sent here may be a product this pharmacy has never held. Receive
        # into our own row, mirroring the supplier's if we have none.
        product = Product.tenant_objects.filter(pk=data["product"]).first()
        if product is None:
            source = get_object_or_404(
                Product.objects.select_related("product_type", "registration"),
                pk=data["product"],
            )
            product = catalog_services.mirror_product(
                organization=request.user.organization,
                source=source,
                performed_by=request.user,
            )

        services.add_receipt_line(
            receipt=receipt,
            product=product,
            uom=_resolve_uom(product, data["uom_code"]),
            received=data["received"],
            accepted=data.get("accepted"),
            rejected=data.get("rejected", 0),
            rejection_reason=data.get("rejection_reason", ""),
            batch_number=data["batch_number"],
            expiry_date=data["expiry_date"],
            unit_cost_base=data.get("unit_cost_base", 0),
            order_line=order_line,
            gtin=data.get("gtin", ""),
            serial=data.get("serial", ""),
        )
        receipt.refresh_from_db()
        return Response(GoodsReceiptSerializer(receipt).data)

    @action(detail=True, methods=["post"])
    def post_receipt(self, request, pk=None):
        """Create the batches and move the stock."""
        receipt = services.post_receipt(receipt=self.get_object(), performed_by=request.user)
        return Response(GoodsReceiptSerializer(receipt).data)

    @action(detail=True, methods=["get"])
    def discrepancies(self, request, pk=None):
        """What differed from the order. The point of the document."""
        return Response(services.discrepancies(self.get_object()))


class TradingRelationshipViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """A supplier's approved customers."""

    serializer_class = TradingRelationshipSerializer

    def get_queryset(self):
        return TradingRelationship.tenant_objects.select_related("customer").all()

    def perform_create(self, serializer):
        serializer.save(organization=self.request.user.organization, created_by=self.request.user)


class CapabilityView(APIView):
    """What this organization may do, derived from held licences.

    The frontend uses this to decide which navigation to show — a retail
    pharmacy has no Distribution, a wholesale pharmacy no Point of sale.
    """

    def get(self, request):
        from core.capabilities import capabilities_of
        from core.models import PremisesLicence

        org = request.user.organization
        if org is None:
            return Response({"capabilities": [], "licences": []})

        return Response(
            {
                "capabilities": sorted(capabilities_of(org)),
                "licences": [
                    {
                        "kind": lic.kind,
                        "number": lic.number,
                        "expiry": lic.expiry,
                        "status": lic.status,
                        "is_valid": lic.is_valid,
                    }
                    for lic in PremisesLicence.objects.filter(organization=org)
                ],
            }
        )
