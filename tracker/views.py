import os
import requests
import numpy as np
import pandas as pd
import math
from datetime import timedelta, datetime
from scipy.stats import linregress

from django.utils import timezone
from django.core.cache import cache
from django.views.generic import TemplateView
from django.db.models import Sum, Case, When, F, DecimalField

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import viewsets

import google.generativeai as genai

from .models import PortfolioPosition, AssetPriceHistory, Transaction, Asset
from .serializers import TransactionSerializer

from celery.result import AsyncResult
from .task import generate_excel_report

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


class CorrelationMatrixView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        tickers = list(PortfolioPosition.objects.filter(user=user)
                       .values_list('asset__ticker', flat=True).distinct())

        if len(tickers) < 2:
            return Response({
                "error": "Для розрахунку матриці ризиків у вашому портфелі має бути щонайменше 2 різних активи."
            }, status=400)

        tickers = [t.upper() for t in tickers]
        tickers.sort()

        cache_key = f"correlation_matrix_user_{user.id}_{'_'.join(tickers)}"
        cached_data = cache.get(cache_key)

        if cached_data:
            return Response(cached_data)

        price_series = {}
        url = "https://api.binance.com/api/v3/klines"

        for ticker in tickers:
            symbol = f"{ticker}USDT"
            params = {
                "symbol": symbol,
                "interval": "1d",
                "limit": 30
            }

            try:
                response = requests.get(url, params=params, timeout=5)
                response.raise_for_status()
                candles = response.json()

                # ОНОВЛЕННЯ: Беремо не тільки ціни, а й дати, щоб Pandas міг їх правильно зіставити
                dates = [datetime.fromtimestamp(candle[0] / 1000.0).date() for candle in candles]
                close_prices = [float(candle[4]) for candle in candles]

                if len(close_prices) >= 5:
                    # Створюємо pandas Series із прив'язкою до дат
                    price_series[ticker] = pd.Series(data=close_prices, index=dates)
            except Exception as e:
                return Response({
                    "error": f"Не вдалося отримати ринкові дані з Binance для тикера {ticker}. Перевірте правильність написання активу."
                }, status=400)

        try:
            # Тепер DataFrame автоматично вирівняє всі масиви по датах (навіть якщо у NVDAB менше історії)
            df = pd.DataFrame(price_series)
            corr_matrix = df.corr()
            matrix_dict = corr_matrix.fillna(0).to_dict()

            response_data = {
                "assets": tickers,
                "correlation_matrix": matrix_dict,
                "message": "Матриця розрахована «на льоту» за даними Binance"
            }

            cache.set(cache_key, response_data, timeout=7200)
            return Response(response_data)

        except Exception as e:
            return Response({
                "error": f"Внутрішня помилка при математичному аналізі даних: {str(e)}"
            }, status=500)


class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user).order_by('-timestamp')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class PortfolioPerformanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        transactions = Transaction.objects.filter(user=request.user).values(
            'asset__ticker', 'transaction_type', 'quantity', 'timestamp'
        )
        if not transactions:
            return Response({"error": "Портфель порожній, немає угод"}, status=400)

        df_tx = pd.DataFrame.from_records(transactions)
        df_tx['date'] = pd.to_datetime(df_tx['timestamp']).dt.date

        df_tx['qty'] = df_tx.apply(
            lambda r: float(r['quantity']) if r['transaction_type'] == 'BUY' else -float(r['quantity']),
            axis=1
        )

        daily_changes = df_tx.groupby(['date', 'asset__ticker'])['qty'].sum().reset_index()
        pivot_balances = daily_changes.pivot(index='date', columns='asset__ticker', values='qty').fillna(0)

        start_date = pivot_balances.index.min()
        end_date = timezone.now().date()
        full_calendar = pd.date_range(start=start_date, end=end_date).date

        pivot_balances = pivot_balances.reindex(full_calendar, fill_value=0)
        cumulative_balances = pivot_balances.cumsum()

        prices = AssetPriceHistory.objects.filter(
            date__gte=start_date,
            asset__ticker__in=cumulative_balances.columns
        ).values('asset__ticker', 'date', 'close_price')

        if not prices:
            return Response({"error": "Немає історичних цін для розрахунку"}, status=400)

        df_prices = pd.DataFrame.from_records(prices)
        df_prices['close_price'] = df_prices['close_price'].astype(float)
        pivot_prices = df_prices.pivot(index='date', columns='asset__ticker', values='close_price')

        pivot_prices = pivot_prices.reindex(full_calendar).ffill().bfill()

        portfolio_value = (cumulative_balances * pivot_prices).sum(axis=1)

        df_perf = pd.DataFrame({'total_value': portfolio_value})

        if len(df_perf) > 1:
            df_perf['daily_return'] = df_perf['total_value'].pct_change().dropna()

            std_dev = df_perf['daily_return'].std()
            if std_dev and not math.isnan(std_dev) and std_dev != 0:
                sharpe_ratio = (df_perf['daily_return'].mean() / std_dev) * np.sqrt(365)
            else:
                sharpe_ratio = 0.0

            rolling_max = df_perf['total_value'].cummax()
            rolling_max = rolling_max.replace(0, np.nan)
            drawdown = (df_perf['total_value'] / rolling_max) - 1.0
            max_drawdown = drawdown.min() * 100
        else:
            sharpe_ratio = 0.0
            max_drawdown = 0.0

        # Фінальна очистка значень від NaN перед відправкою у JSON
        if math.isnan(sharpe_ratio): sharpe_ratio = 0.0
        if math.isnan(max_drawdown): max_drawdown = 0.0

        chart_data = [
            {"date": str(date), "total_value": round(val, 2)}
            for date, val in portfolio_value.items()
        ]

        return Response({
            "chart_data": chart_data,
            "risk_metrics": {
                "sharpe_ratio": round(sharpe_ratio, 2),
                "max_drawdown": round(max_drawdown, 2)
            },
            "message": "Дані для графіка та метрики ризиків успішно згенеровані"
        })


class DashboardView(TemplateView):
    template_name = 'tracker/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            tickers = PortfolioPosition.objects.filter(
                user=self.request.user
            ).values_list('asset__ticker', flat=True).distinct()
            context['user_assets'] = list(tickers)
            context['all_assets'] = Asset.objects.all()

            transactions = Transaction.objects.filter(user=self.request.user).order_by('timestamp')
            portfolio_data = {}

            for tx in transactions:
                ticker = tx.asset.ticker
                qty = float(tx.quantity)
                price = float(tx.execution_price or 0)

                if ticker not in portfolio_data:
                    portfolio_data[ticker] = {'qty': 0.0, 'avg_price': 0.0}

                curr_qty = portfolio_data[ticker]['qty']
                curr_avg = portfolio_data[ticker]['avg_price']

                if tx.transaction_type == 'BUY':
                    new_qty = curr_qty + qty
                    new_avg = ((curr_qty * curr_avg) + (qty * price)) / new_qty if new_qty > 0 else 0

                    portfolio_data[ticker]['qty'] = new_qty
                    portfolio_data[ticker]['avg_price'] = new_avg
                elif tx.transaction_type == 'SELL':
                    new_qty = curr_qty - qty
                    portfolio_data[ticker]['qty'] = new_qty

            user_balances = {}
            user_avg_prices = {}
            for ticker, data in portfolio_data.items():
                if data['qty'] > 0:
                    user_balances[ticker] = data['qty']
                    user_avg_prices[ticker] = data['avg_price']

            context['user_balances'] = user_balances
            context['user_avg_prices'] = user_avg_prices

        else:
            context['user_assets'] = []
            context['all_assets'] = []
            context['user_balances'] = {}
            context['user_avg_prices'] = {}

        return context


class PriceForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ticker):
        symbol = f"{ticker.upper()}USDT"
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": "1d",
            "limit": 30
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            return Response(
                {"error": f"Не вдалося отримати дані з Binance для {ticker}. Можливо, монета не підтримується."},
                status=400)

        if len(data) < 5:
            return Response({"error": f"Недостатньо історичних даних для {ticker}"}, status=400)

        dates = []
        prices = []
        for candle in data:
            date_obj = datetime.fromtimestamp(candle[0] / 1000.0).date()
            dates.append(date_obj)
            prices.append(float(candle[4]))

        x_days = list(range(len(prices)))
        slope, intercept, r_value, p_value, std_err = linregress(x_days, prices)

        forecast_days = 7
        last_date = dates[-1]

        future_dates = [(last_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, forecast_days + 1)]
        future_prices = [round(slope * (len(prices) + i) + intercept, 2) for i in range(forecast_days)]

        return Response({
            "ticker": ticker.upper(),
            "trend_direction": "UP" if slope > 0 else "DOWN",
            "historical": {
                "dates": [d.strftime('%Y-%m-%d') for d in dates],
                "prices": prices
            },
            "forecast": {
                "dates": future_dates,
                "prices": future_prices
            },
            "message": "Прогноз побудовано за живими даними Binance"
        })


class AIAssistantView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not GEMINI_API_KEY:
            return Response({"error": "Сервер ШІ тимчасово недоступний (відсутній API-ключ)."}, status=500)

        action = request.data.get('action')
        payload = request.data.get('payload')

        model = genai.GenerativeModel('gemini-2.5-flash-lite')

        base_context = (
            "Ти професійний фінансовий та криптоаналітик. "
            "Звертайся до користувача як до користувача, без імен. "
            "Відповідай коротко, без зайвої води, структуруй текст за допомогою списків. "
            "Пиши професійно та лаконічно."
        )

        if action == 'audit':
            prompt = f"{base_context}\nПроведи аудит цього портфеля: {payload}. Оціни розподіл активів (чи немає критичного перекосу в одну монету), прокоментуй загальний прибуток (PNL) та дай 2 короткі поради з ризик-менеджменту."

        elif action == 'matrix':
            prompt = f"{base_context}\nПроаналізуй матрицю кореляції Пірсона: {payload}. Поясни простою мовою: які монети рухаються однаково (збільшують ризик просідання всього портфеля), а які мають низьку або близьку до нуля кореляцію (служать хорошим захистом). Зроби короткий висновок."

        elif action == 'forecast':
            prompt = f"{base_context}\nЯ побудував ML-прогноз (Linear Regression) для криптоактиву. Дані: {payload}. Поясни, який математичний тренд показує модель і чи варто зараз здійснювати купівлю, спираючись суворо на цей тренд."
        else:
            return Response({"error": "Невідома дія"}, status=400)

        try:
            response = model.generate_content(prompt)
            return Response({"response": response.text})
        except Exception as e:
            return Response({"error": f"Помилка при запиті до Gemini: {str(e)}"}, status=500)


class ExportReportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        task = generate_excel_report.delay(request.user.id)
        return Response({"task_id": task.id, "message": "Генерація Excel-звіту розпочата"})


class ReportStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task_result = AsyncResult(task_id)

        if task_result.ready():
            if task_result.successful():
                file_url = task_result.result
                if file_url:
                    return Response({"status": "ready", "download_url": file_url})
                return Response({"status": "failed", "error": "Немає даних для експорту"})
            return Response({"status": "failed", "error": "Помилка при створенні звіту"})

        return Response({"status": "processing"})