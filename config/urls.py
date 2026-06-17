from django.contrib import admin
from django.urls import path, include # Добавили include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('tracker.urls')), # Подключили роуты нашего трекера
]