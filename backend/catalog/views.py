"""Catalog endpoints.

Views authorize, deserialize, call a service, serialize. No business
logic. Every queryset uses tenant_objects — never objects.
"""

from __future__ import annotations

from django_filters import rest_framework as filters
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.gs1 import GS1ParseError
from catalog.models import Category, Product, ProductType
from catalog.serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ProductTypeSerializer,
    ProductWriteSerializer,
    ScanInputSerializer,
    ScanResultSerializer,
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


class ProductTypeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductTypeSerializer

    def get_queryset(self):
        return ProductType.tenant_objects.prefetch_related("attributes").all()


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CategorySerializer

    def get_queryset(self):
        return Category.tenant_objects.all()


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
