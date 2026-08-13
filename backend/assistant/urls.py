from django.urls import path
from rest_framework.routers import DefaultRouter

from assistant import views

router = DefaultRouter()
router.register("proposals", views.ProposalViewSet, basename="proposal")

urlpatterns = [
    path("assistant/ask/", views.AskView.as_view(), name="assistant-ask"),
    path(
        "assistant/proposals/<uuid:proposal_id>/decide/",
        views.ConfirmView.as_view(),
        name="assistant-decide",
    ),
] + router.urls
