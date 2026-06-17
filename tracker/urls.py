from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CorrelationMatrixView,
    TransactionViewSet,
    PortfolioPerformanceView,
    DashboardView,
    PriceForecastView,
    AIAssistantView  # <-- Добавили импорт ИИ-контроллера
)

router = DefaultRouter()
router.register(r'transactions', TransactionViewSet, basename='transaction')

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('api/', include(router.urls)),

    # Аналитика
    path('api/analytics/correlation/', CorrelationMatrixView.as_view(), name='correlation-matrix'),
    path('api/analytics/performance/', PortfolioPerformanceView.as_view(), name='portfolio-performance'),
    path('api/analytics/forecast/<str:ticker>/', PriceForecastView.as_view(), name='price-forecast'),

    # <-- Добавили новый роут для ИИ-ассистента
    path('api/ai/', AIAssistantView.as_view(), name='ai-assistant'),
]