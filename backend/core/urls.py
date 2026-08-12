from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", views.me, name="me"),
    path("", include("catalog.urls")),
    path("", include("inventory.urls")),
    path("", include("sales.urls")),
    path("", include("commerce.urls")),
]
