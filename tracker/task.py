import os
import requests
import pandas as pd
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from celery import shared_task

from .models import Asset, AssetPriceHistory, Transaction


@shared_task
def fetch_daily_crypto_prices():
    crypto_assets = Asset.objects.filter(asset_type='CRYPTO')
    if not crypto_assets.exists():
        return "Немає криптоактивів для оновлення"

    base_url = "https://api.binance.com/api/v3/ticker/price"
    today = timezone.now().date()
    updated_count = 0

    for asset in crypto_assets:
        symbol = f"{asset.ticker.upper()}USDT"

        try:
            response = requests.get(base_url, params={'symbol': symbol}, timeout=5)
            data = response.json()

            if 'price' in data:
                price = Decimal(data['price'])

                AssetPriceHistory.objects.update_or_create(
                    asset=asset,
                    date=today,
                    defaults={'close_price': price}
                )
                updated_count += 1
        except Exception as e:
            print(f"Помилка оновлення ціни для {asset.ticker}: {str(e)}")

    return f"Успішно оновлено цін: {updated_count}"


@shared_task
def generate_csv_report(user_id):
    transactions = Transaction.objects.filter(user_id=user_id).values(
        'timestamp', 'asset__ticker', 'transaction_type', 'quantity', 'execution_price'
    ).order_by('-timestamp')

    if not transactions:
        return None

    df = pd.DataFrame.from_records(transactions)

    df.rename(columns={
        'timestamp': 'Дата та час',
        'asset__ticker': 'Актив',
        'transaction_type': 'Тип операції',
        'quantity': 'Кількість',
        'execution_price': 'Ціна виконання ($)'
    }, inplace=True)

    df['Кількість'] = df['Кількість'].astype(float)
    df['Ціна виконання ($)'] = df['Ціна виконання ($)'].astype(float)
    df['Загальна сума ($)'] = (df['Кількість'] * df['Ціна виконання ($)']).round(2)

    df['Дата та час'] = df['Дата та час'].dt.tz_localize(None)

    reports_dir = os.path.join(settings.MEDIA_ROOT, 'reports')
    os.makedirs(reports_dir, exist_ok=True)

    filename = f"finance_report_user_{user_id}.csv"
    filepath = os.path.join(reports_dir, filename)

    df.to_csv(filepath, index=False, encoding='utf-8-sig')

    return f"/media/reports/{filename}"