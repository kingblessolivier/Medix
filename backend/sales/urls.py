from django.urls import path
from rest_framework.routers import DefaultRouter

from sales import views

router = DefaultRouter()
router.register("sales", views.SaleViewSet, basename="sale")
router.register("prescriptions", views.PrescriptionViewSet, basename="prescription")
router.register("tills", views.TillViewSet, basename="till")
router.register("shifts", views.ShiftViewSet, basename="shift")
router.register("patients", views.PatientViewSet, basename="patient")
router.register("allergies", views.PatientAllergyViewSet, basename="patient-allergy")
router.register("prescribers", views.PrescriberViewSet, basename="prescriber")
router.register("tax-rules", views.TaxRuleViewSet, basename="tax-rule")

urlpatterns = [
    path("controlled-register/", views.ControlledRegisterView.as_view(), name="controlled-register"),
    path("sales/returns/", views.SaleReturnView.as_view(), name="sale-return"),
    path("fiscal/exceptions/", views.FiscalExceptionView.as_view(), name="fiscal-exceptions"),
    path(
        "callbacks/payments/<str:provider>/",
        views.PaymentCallbackView.as_view(),
        name="payment-callback",
    ),
] + router.urls
