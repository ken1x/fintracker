from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CorrelationMatrixView,
    TransactionViewSet,
    PortfolioPerformanceView,
    DashboardView,
    PriceForecastView,
    AIAssistantView,
    ExportReportView,
    ReportStatusView
)

router = DefaultRouter()
router.register(r'transactions', TransactionViewSet, basename='transaction')

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('api/', include(router.urls)),

    path('api/analytics/correlation/', CorrelationMatrixView.as_view(), name='correlation-matrix'),
    path('api/analytics/performance/', PortfolioPerformanceView.as_view(), name='portfolio-performance'),
    path('api/analytics/forecast/<str:ticker>/', PriceForecastView.as_view(), name='price-forecast'),

    path('api/ai/', AIAssistantView.as_view(), name='ai-assistant'),

    path('api/export/', ExportReportView.as_view(), name='export-report'),
    path('api/export/status/<str:task_id>/', ReportStatusView.as_view(), name='export-status'),
]