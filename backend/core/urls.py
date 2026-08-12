from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core import alerts_views, views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("alerts/", alerts_views.AlertView.as_view(), name="alerts"),
    path(
        "alerts/product/<uuid:product_id>/",
        alerts_views.ProductAlertView.as_view(),
        name="product-alerts",
    ),
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", views.me, name="me"),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    path("", include("sales.urls")),
    path("", include("commerce.urls")),
    path("", include("documents.urls")),
]
