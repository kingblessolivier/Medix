from django.urls import path
from rest_framework.routers import DefaultRouter

from catalog import views

router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("product-types", views.ProductTypeViewSet, basename="product-type")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("manufacturers", views.ManufacturerViewSet, basename="manufacturer")
router.register("units", views.UnitOfMeasureViewSet, basename="unit-of-measure")
router.register("product-images", views.ProductImageViewSet, basename="product-image")
router.register(
    "product-registrations",
    views.ProductRegistrationViewSet,
    basename="product-registration",
)
router.register(
    "clinical-attributes",
    views.ClinicalAttributeViewSet,
    basename="clinical-attribute",
)

urlpatterns = [path("scan/", views.ScanView.as_view(), name="scan")] + router.urls
