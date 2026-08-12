from django.urls import path
from rest_framework.routers import DefaultRouter

from commerce import views

router = DefaultRouter()
router.register("marketplace", views.MarketplaceViewSet, basename="marketplace")
router.register("listings", views.ListingViewSet, basename="listing")
router.register("purchase-orders", views.PurchaseOrderViewSet, basename="purchase-order")
router.register("goods-receipts", views.GoodsReceiptViewSet, basename="goods-receipt")
router.register("customers", views.TradingRelationshipViewSet, basename="customer")
router.register(
    "import-documents", views.ImportDocumentViewSet, basename="import-document"
)

urlpatterns = [
    path("capabilities/", views.CapabilityView.as_view(), name="capabilities"),
    path("batches/release/", views.BatchReleaseView.as_view(), name="batch-release"),
] + router.urls
