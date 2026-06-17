import os
import requests
import pandas as pd
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

# ==========================================
# ИНИЦИАЛИЗАЦИЯ ИИ (GEMINI)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


# ==========================================
# 1. АНАЛИТИКА: МАТРИЦА КОРРЕЛЯЦИИ
# ==========================================
class CorrelationMatrixView(APIView):
    """
    API для расчета матрицы корреляции активов пользователя на лету
    с использованием исторических данных Binance и кэширования в Redis.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Извлекаем из базы данных только те тикеры, которые реально есть в сделках юзера
        tickers = list(PortfolioPosition.objects.filter(user=user)
                       .values_list('asset__ticker', flat=True).distinct())

        # Защита: для корреляции нужно минимум 2 актива
        if len(tickers) < 2:
            return Response({
                "error": "Для расчета матрицы рисков в вашем портфеле должно быть минимум 2 различных актива."
            }, status=400)

        # Приводим к верхнему регистру и сортируем, чтобы ключ кэша всегда был идентичным
        tickers = [t.upper() for t in tickers]
        tickers.sort()

        # Формируем уникальный ключ кэша для текущего состава портфеля
        cache_key = f"correlation_matrix_user_{user.id}_{'_'.join(tickers)}"
        cached_data = cache.get(cache_key)

        if cached_data:
            # Если данные найдены в Redis — отдаем их моментально (Cache Hit)
            return Response(cached_data)

        # Если в кэше пусто (Cache Miss) — идем собирать данные с Binance API
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
                close_prices = [float(candle[4]) for candle in candles]

                if len(close_prices) >= 5:
                    price_series[ticker] = close_prices
            except Exception as e:
                return Response({
                    "error": f"Не удалось получить рыночные данные с Binance для тикера {ticker}. Проверьте правильность написания актива."
                }, status=400)

        try:
            # Создаем DataFrame, где колонки — это тикеры, а строки — цены за 30 дней
            df = pd.DataFrame(price_series)
            corr_matrix = df.corr()
            matrix_dict = corr_matrix.fillna(0).to_dict()

            response_data = {
                "assets": tickers,
                "correlation_matrix": matrix_dict,
                "message": "Матрица рассчитана на лету по данным Binance"
            }

            # Сохраняем результат в кэш Redis на 2 часа (7200 секунд)
            cache.set(cache_key, response_data, timeout=7200)
            return Response(response_data)

        except Exception as e:
            return Response({
                "error": f"Внутренняя ошибка при математическом анализе данных: {str(e)}"
            }, status=500)


# ==========================================
# 2. CRUD: СДЕЛКИ (TRANSACTIONS)
# ==========================================
class TransactionViewSet(viewsets.ModelViewSet):
    """
    CRUD-эндпоинт для сделок. Юзер видит и создает только свои транзакции.
    """
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user).order_by('-timestamp')

    def perform_create(self, serializer):
        # Автоматически привязываем сделку к тому юзеру, который отправил запрос
        serializer.save(user=self.request.user)


# ==========================================
# 3. АНАЛИТИКА: ИСТОРИЯ ПОРТФЕЛЯ
# ==========================================
class PortfolioPerformanceView(APIView):
    """
    API для построения графика стоимости портфеля.
    Использует Pandas для наложения истории балансов на историю цен.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        transactions = Transaction.objects.filter(user=request.user).values(
            'asset__ticker', 'transaction_type', 'quantity', 'timestamp'
        )
        if not transactions:
            return Response({"error": "Портфель пуст, нет сделок"}, status=400)

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
            return Response({"error": "Нет исторических цен для расчета"}, status=400)

        df_prices = pd.DataFrame.from_records(prices)
        df_prices['close_price'] = df_prices['close_price'].astype(float)
        pivot_prices = df_prices.pivot(index='date', columns='asset__ticker', values='close_price')

        pivot_prices = pivot_prices.reindex(full_calendar).ffill().bfill()

        portfolio_value = (cumulative_balances * pivot_prices).sum(axis=1)

        chart_data = [
            {"date": str(date), "total_value": round(val, 2)}
            for date, val in portfolio_value.items()
        ]

        return Response({
            "chart_data": chart_data,
            "message": "Данные для графика успешно сгенерированы"
        })


# ==========================================
# 4. DASHBOARD КОНТЕКСТ
# ==========================================
class DashboardView(TemplateView):
    template_name = 'tracker/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            # Списки активов
            tickers = PortfolioPosition.objects.filter(
                user=self.request.user
            ).values_list('asset__ticker', flat=True).distinct()
            context['user_assets'] = list(tickers)
            context['all_assets'] = Asset.objects.all()

            # Вычисляем количество монет и Средневзвешенную цену покупки (WAC)
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


# ==========================================
# 5. АНАЛИТИКА: ML ПРОГНОЗ
# ==========================================
class PriceForecastView(APIView):
    """
    API для прогнозирования цены актива на 7 дней вперед
    с помощью линейной регрессии на основе реальных данных Binance.
    """
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
                {"error": f"Не удалось получить данные с Binance для {ticker}. Возможно, монета не поддерживается."},
                status=400)

        if len(data) < 5:
            return Response({"error": f"Недостаточно исторических данных по {ticker}"}, status=400)

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
            "message": "Прогноз построен по живым данным Binance"
        })


# ==========================================
# 6. ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ (GEMINI)
# ==========================================
class AIAssistantView(APIView):
    """
    Универсальный ИИ-ассистент на базе Google Gemini.
    Принимает тип запроса и контекст данных, возвращает финансовую аналитику.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not GEMINI_API_KEY:
            return Response({"error": "Сервер ИИ временно недоступен (отсутствует API ключ)."}, status=500)

        action = request.data.get('action')
        payload = request.data.get('payload')

        # Используем быструю и точную модель для работы с текстом и цифрами
        model = genai.GenerativeModel('gemini-3.5-flash')

        # Системный промпт с контекстом
        base_context = (
            "Ты профессиональный финансовый и крипто-аналитик. "
            "Обращайся к пользователю как к пользователю, без имен. "
            "Отвечай кратко, без лишней воды, структурируй текст с помощью списков. "
            "Пиши профессионально и емко."
        )

        if action == 'audit':
            prompt = f"{base_context}\nПроведи аудит этого портфеля: {payload}. Оцени распределение активов (нет ли критического перекоса в одну монету), прокомментируй общую прибыль (PNL) и дай 2 коротких совета по риск-менеджменту."

        elif action == 'matrix':
            prompt = f"{base_context}\nПроанализируй матрицу корреляции Пирсона: {payload}. Объясни простым языком: какие монеты движутся одинаково (увеличивают риск просадки всего портфеля), а какие имеют низкую или близкую к нулю корреляцию (служат хорошей защитой). Сделай краткий вывод."

        elif action == 'forecast':
            prompt = f"{base_context}\nЯ построил ML-прогноз (Linear Regression) для криптоактива. Данные: {payload}. Объясни, какой математический тренд показывает модель и стоит ли сейчас совершать покупку, опираясь строго на этот тренд."
        else:
            return Response({"error": "Неизвестное действие"}, status=400)

        try:
            response = model.generate_content(prompt)
            return Response({"response": response.text})
        except Exception as e:
            return Response({"error": f"Ошибка при запросе к Gemini: {str(e)}"}, status=500)