from django.urls import path
from rest_framework.routers import DefaultRouter

from insurance import views

router = DefaultRouter()
router.register("schemes", views.SchemeViewSet, basename="scheme")
router.register("scheme-contracts", views.SchemeContractViewSet, basename="scheme-contract")
router.register("coverage-rules", views.CoverageRuleViewSet, basename="coverage-rule")
router.register("members", views.MemberViewSet, basename="member")
router.register("claims", views.ClaimViewSet, basename="claim")
router.register(
    "capitation-receipts",
    views.CapitationReceiptViewSet,
    basename="capitation-receipt",
)

urlpatterns = [
    path("eligibility/", views.EligibilityView.as_view(), name="eligibility"),
    path("sales/<uuid:sale_id>/cover/", views.SaleCoverView.as_view(), name="sale-cover"),
    path(
        "insurance/receivables/",
        views.SchemeReceivablesView.as_view(),
        name="scheme-receivables",
    ),
    path(
        "insurance/capitation/<uuid:contract_id>/",
        views.CapitationUtilisationView.as_view(),
        name="capitation-utilisation",
    ),
] + router.urls
