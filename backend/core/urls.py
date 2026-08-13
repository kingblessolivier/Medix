from django.urls import include, path

from rest_framework.routers import DefaultRouter

from core import alerts_views, sync_views, views

router = DefaultRouter()
router.register("alert-rules", alerts_views.AlertRuleViewSet, basename="alert-rule")
router.register("devices", sync_views.DeviceViewSet, basename="device")
router.register("sensors", sync_views.SensorViewSet, basename="sensor")
router.register("excursions", sync_views.ExcursionViewSet, basename="excursion")
router.register(
    "controlled-quotas", alerts_views.ControlledQuotaViewSet, basename="controlled-quota"
)

urlpatterns = [
    path("health/", views.health, name="health"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("sync/", sync_views.SyncView.as_view(), name="sync"),
    path("sync/status/", sync_views.SyncStatusView.as_view(), name="sync-status"),
    path("telemetry/", sync_views.TelemetryView.as_view(), name="telemetry"),
    path(
        "excursions/<uuid:excursion_id>/resolve/",
        sync_views.ResolveExcursionView.as_view(),
        name="excursion-resolve",
    ),
    path("pharmacies/", views.PharmacyViewSet.as_view({"get": "list"}), name="pharmacies"),
    path(
        "pharmacies/register/",
        views.RegisterPharmacyView.as_view(),
        name="pharmacy-register",
    ),
    path("alerts/", alerts_views.AlertView.as_view(), name="alerts"),
    path(
        "alerts/product/<uuid:product_id>/",
        alerts_views.ProductAlertView.as_view(),
        name="product-alerts",
    ),
    path("compliance/", alerts_views.ComplianceView.as_view(), name="compliance"),
    path(
        "compliance/extract/",
        alerts_views.RegulatorExtractView.as_view(),
        name="regulator-extract",
    ),
    path("auth/token/", views.ThrottledTokenObtainPairView.as_view(), name="token-obtain"),
    path(
        "auth/token/refresh/",
        views.ThrottledTokenRefreshView.as_view(),
        name="token-refresh",
    ),
    path("auth/me/", views.me, name="me"),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    path("", include("sales.urls")),
    path("", include("commerce.urls")),
    path("", include("documents.urls")),
    path("", include("finance.urls")),
    path("", include("insurance.urls")),
    path("", include("assistant.urls")),
] + router.urls
