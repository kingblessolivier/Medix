from django.urls import path
from rest_framework.routers import DefaultRouter

from commerce import views

router = DefaultRouter()
router.register("marketplace", views.MarketplaceViewSet, basename="marketplace")
router.register("listings", views.ListingViewSet, basename="listing")
router.register("purchase-orders", views.PurchaseOrderViewSet, basename="purchase-order")
router.register("goods-receipts", views.GoodsReceiptViewSet, basename="goods-receipt")
router.register("customers", views.TradingRelationshipViewSet, basename="customer")

urlpatterns = [
    path("capabilities/", views.CapabilityView.as_view(), name="capabilities"),
] + router.urls
