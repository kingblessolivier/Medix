from django.urls import path
from rest_framework.routers import DefaultRouter

from finance import views

router = DefaultRouter()
router.register("expense-categories", views.ExpenseCategoryViewSet, basename="expense-category")
router.register("expenses", views.ExpenseViewSet, basename="expense")
router.register("write-offs", views.WriteOffViewSet, basename="write-off")

urlpatterns = [
    path("finance/period/", views.PeriodReportView.as_view(), name="finance-period"),
    path("finance/dashboard/", views.DashboardView.as_view(), name="finance-dashboard"),
    path("finance/receivables/", views.ReceivablesView.as_view(), name="finance-receivables"),
] + router.urls
