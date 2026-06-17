import requests
from celery import shared_task
from django.utils import timezone
from decimal import Decimal
from .models import Asset, AssetPriceHistory


@shared_task
def fetch_daily_crypto_prices():
    """
    Фоновая задача: собирает текущие цены криптовалют из БД через Binance API
    и сохраняет их как цену закрытия за сегодняшний день.
    """
    # Получаем все активы типа "Криптовалюта"
    crypto_assets = Asset.objects.filter(asset_type='CRYPTO')
    if not crypto_assets.exists():
        return "Нет криптоактивов для обновления"

    # API Binance для получения текущей цены
    base_url = "https://api.binance.com/api/v3/ticker/price"
    today = timezone.now().date()
    updated_count = 0

    for asset in crypto_assets:
        # Binance использует тикеры вида BTCUSDT
        symbol = f"{asset.ticker.upper()}USDT"

        try:
            response = requests.get(base_url, params={'symbol': symbol}, timeout=5)
            data = response.json()

            if 'price' in data:
                price = Decimal(data['price'])

                # Используем update_or_create, чтобы не плодить дубли за один день
                AssetPriceHistory.objects.update_or_create(
                    asset=asset,
                    date=today,
                    defaults={'close_price': price}
                )
                updated_count += 1
        except Exception as e:
            # В проде здесь пишем логирование (logging.error), но пока оставим так
            print(f"Ошибка обновления цены для {asset.ticker}: {str(e)}")

    return f"Успешно обновлено цен: {updated_count}"