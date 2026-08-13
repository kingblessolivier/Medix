from django.urls import path
from rest_framework.routers import DefaultRouter

from inventory import views

router = DefaultRouter()
router.register("locations", views.LocationViewSet, basename="location")
router.register("batches", views.BatchViewSet, basename="batch")
router.register("stock", views.StockViewSet, basename="stock")
router.register("stock-movements", views.StockMovementViewSet, basename="stock-movement")

# Explicit action paths come first: the router's `stock/{pk}/` detail
# route would otherwise match `stock/adjust/` with pk="adjust".
urlpatterns = [
    path("allocations/preview/", views.AllocationPreviewView.as_view(), name="allocation-preview"),
    path("stock/receive/", views.ReceiveStockView.as_view(), name="stock-receive"),
    path("stock/adjust/", views.AdjustStockView.as_view(), name="stock-adjust"),
    # transfer · quarantine · supplier-return · recall
    path(
        "stock/<slug:action>/",
        views.StockActionView.as_view(),
        name="stock-action",
    ),
    path(
        "batches/<uuid:batch_id>/trace/",
        views.BatchTraceView.as_view(),
        name="batch-trace",
    ),
] + router.urls
