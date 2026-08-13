from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from rest_framework.routers import DefaultRouter

from core import alerts_views, views

router = DefaultRouter()
router.register("alert-rules", alerts_views.AlertRuleViewSet, basename="alert-rule")
router.register(
    "controlled-quotas", alerts_views.ControlledQuotaViewSet, basename="controlled-quota"
)

urlpatterns = [
    path("health/", views.health, name="health"),
    path("search/", views.SearchView.as_view(), name="search"),
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
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", views.me, name="me"),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    path("", include("sales.urls")),
    path("", include("commerce.urls")),
    path("", include("documents.urls")),
    path("", include("finance.urls")),
] + router.urls
