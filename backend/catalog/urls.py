from django.urls import path
from rest_framework.routers import DefaultRouter

from catalog import views

router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("product-types", views.ProductTypeViewSet, basename="product-type")
router.register("categories", views.CategoryViewSet, basename="category")

urlpatterns = [path("scan/", views.ScanView.as_view(), name="scan")] + router.urls
