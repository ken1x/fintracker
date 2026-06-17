import os
from celery import Celery

# Задаем переменную окружения, чтобы Celery знал, где искать настройки Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Создаем экземпляр приложения Celery
app = Celery('config')

# Загружаем настройки из settings.py (все переменные с префиксом CELERY_)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически ищем задачи (tasks.py) во всех зарегистрированных приложениях (tracker)
app.autodiscover_tasks()

