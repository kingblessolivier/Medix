"""Catalog endpoints.

Views authorize, deserialize, call a service, serialize. No business
logic. Every queryset uses tenant_objects — never objects.
"""

from __future__ import annotations

from django.db.models import Count
from django_filters import rest_framework as filters
from rest_framework import viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.gs1 import GS1ParseError
from catalog import services as catalog_services
from catalog.models import (
    Category,
    ClinicalAttribute,
    Manufacturer,
    Product,
    ProductImage,
    ProductRegistration,
    ProductType,
    UnitOfMeasure,
)
from catalog.serializers import (
    CategorySerializer,
    ClinicalAttributeSerializer,
    ManufacturerSerializer,
    ProductDetailSerializer,
    ProductImageSerializer,
    ProductListSerializer,
    ProductRegistrationSerializer,
    ProductTypeSerializer,
    ProductWriteSerializer,
    ScanInputSerializer,
    ScanResultSerializer,
    UnitOfMeasureWriteSerializer,
)
from catalog.services import resolve_scan
from core.exceptions import DomainError


class ProductFilter(filters.FilterSet):
    """Explicit whitelist. No arbitrary ORM traversal from query params."""

    product_type = filters.CharFilter(field_name="product_type__code")
    category = filters.UUIDFilter(field_name="category_id")

    class Meta:
        model = Product
        fields = ["legal_status", "tax_treatment", "cold_chain", "is_active"]


class ProductViewSet(viewsets.ModelViewSet):
    filter_backends = [filters.DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProductFilter
    search_fields = ["name", "generic_name", "brand", "gtin"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        return (
            Product.tenant_objects.select_related("product_type", "category")
            .prefetch_related("units", "registration")
            .all()
        )

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProductWriteSerializer
        if self.action == "retrieve":
            return ProductDetailSerializer
        return ProductListSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)






class ScanView(APIView):
    """Resolve a scanned GS1 barcode to a product and batch.

    Fills batch and expiry at receiving, and resolves the exact batch at
    point of sale. A malformed barcode returns 422 with the reason; an
    unrecognised GTIN returns 200 with matched=false and whatever the
    barcode did carry, so the operator can decide.
    """

    def post(self, request):
        payload = ScanInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            result = resolve_scan(request.user.organization, payload.validated_data["code"])
        except GS1ParseError as exc:
            raise DomainError(str(exc), code="invalid_barcode")

        return Response(ScanResultSerializer(result).data)


class ManufacturerViewSet(viewsets.ModelViewSet):
    """Who makes what, and whether they are GMP certified."""

    serializer_class = ManufacturerSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_fields = ["gmp_certified", "country_of_origin", "is_active"]

    def get_queryset(self):
        return Manufacturer.tenant_objects.annotate(
            product_count=Count("products")
        ).order_by("name")

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        """Deactivated, not deleted.

        Products point at it with PROTECT, and a manufacturer that made
        stock still on a shelf must remain nameable. Deactivating keeps
        the record and takes it out of the pickers.
        """
        instance.is_active = False
        instance.save(update_fields=["is_active", "modified_at"])


class ProductTypeViewSet(viewsets.ModelViewSet):
    serializer_class = ProductTypeSerializer

    def get_queryset(self):
        return ProductType.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class CategoryViewSet(viewsets.ModelViewSet):
    """Therapeutic classification. A pharmacy adds its own."""

    serializer_class = CategorySerializer

    def get_queryset(self):
        return Category.tenant_objects.all()

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )

    def perform_destroy(self, instance):
        if instance.products.exists():
            raise DomainError(
                f"{instance.name} still has products.", code="category_in_use"
            )
        instance.delete()


class UnitOfMeasureViewSet(viewsets.ModelViewSet):
    """The packaging chain — carton, pack, blister, unit.

    Validated as a whole after every write. A chain with two base units
    or two levels sharing a factor would corrupt every quantity stored
    against the product, and the failure would be silent.
    """

    serializer_class = UnitOfMeasureWriteSerializer

    def get_queryset(self):
        queryset = UnitOfMeasure.tenant_objects.select_related("product")
        product = self.request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)
        return queryset.order_by("-factor_to_base")

    def perform_create(self, serializer):
        unit = serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )
        catalog_services.validate_uom_chain(unit.product)

    def perform_update(self, serializer):
        unit = serializer.save()
        catalog_services.validate_uom_chain(unit.product)

    def perform_destroy(self, instance):
        if instance.is_base:
            raise DomainError(
                "The base unit cannot be removed — every quantity is stored in it.",
                code="base_uom_required",
            )
        product = instance.product
        instance.delete()
        catalog_services.validate_uom_chain(product)


class ProductImageViewSet(viewsets.ModelViewSet):
    """Pack photography. Verification, not decoration."""

    serializer_class = ProductImageSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        queryset = ProductImage.tenant_objects.select_related("product")
        product = self.request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)
        return queryset

    def perform_create(self, serializer):
        image = serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )
        self._enforce_single_primary(image)

    def perform_update(self, serializer):
        self._enforce_single_primary(serializer.save())

    def _enforce_single_primary(self, image):
        """Exactly one primary per product, enforced by demoting the rest.

        A unique constraint alone would make setting a new primary a
        two-step dance the client has to get right; doing it here means
        the last write wins, which is what the person clicking expects.
        """
        if not image.is_primary:
            return
        ProductImage.objects.filter(product=image.product, is_primary=True).exclude(
            pk=image.pk
        ).update(is_primary=False)


class ProductRegistrationViewSet(viewsets.ModelViewSet):
    """Rwanda FDA registration. Expiry blocks listing and dispensing."""

    serializer_class = ProductRegistrationSerializer

    def get_queryset(self):
        queryset = ProductRegistration.tenant_objects.select_related("product")
        product = self.request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )


class ClinicalAttributeViewSet(viewsets.ModelViewSet):
    """Sourced, effective-dated clinical facts.

    Superseding rather than editing is the intended workflow: close the
    current row with `effective_to` and add a new one. Editing in place
    would rewrite what applied when a past dispensing happened.
    """

    serializer_class = ClinicalAttributeSerializer

    def get_queryset(self):
        queryset = ClinicalAttribute.tenant_objects.select_related("product")
        product = self.request.query_params.get("product")
        kind = self.request.query_params.get("kind")
        if product:
            queryset = queryset.filter(product_id=product)
        if kind:
            queryset = queryset.filter(kind=kind)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            organization=self.request.user.organization, created_by=self.request.user
        )
