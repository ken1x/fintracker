from django.apps import AppConfig

class TrackerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracker'

    def ready(self):
        # Этот импорт внутри метода ready подключает наши сигналы при старте сервера
        import tracker.signals