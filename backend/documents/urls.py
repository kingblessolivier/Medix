from rest_framework.routers import DefaultRouter

from documents import views

router = DefaultRouter()
router.register("documents", views.DocumentViewSet, basename="document")

urlpatterns = router.urls
