from django.db import models
from django.contrib.auth.models import User


class Asset(models.Model):
    ASSET_TYPES = (
        ('CRYPTO', 'Криптовалюта'),
        ('FIAT', 'Фіатна валюта'),
    )

    ticker = models.CharField(max_length=10, unique=True, db_index=True, verbose_name="Тикер")
    name = models.CharField(max_length=100, verbose_name="Повна назва")
    asset_type = models.CharField(max_length=10, choices=ASSET_TYPES, verbose_name="Тип активу")

    class Meta:
        verbose_name = "Актив"
        verbose_name_plural = "Активи"

    def __str__(self) -> str:
        return f"{self.ticker} ({self.name})"


class AssetPriceHistory(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="price_history", verbose_name="Актив")
    date = models.DateField(verbose_name="Дата котирування")
    close_price = models.DecimalField(max_digits=18, decimal_places=8, verbose_name="Ціна закриття")

    class Meta:
        verbose_name = "Історія ціни"
        verbose_name_plural = "Історії цін"
        unique_together = ('asset', 'date')
        indexes = [
            models.Index(fields=['date']),
        ]

    def __str__(self) -> str:
        return f"{self.asset.ticker} на {self.date}: {self.close_price}"


class Transaction(models.Model):
    TRANSACTION_TYPES = (
        ('BUY', 'Купівля'),
        ('SELL', 'Продаж'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="transactions", verbose_name="Користувач")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="transactions", verbose_name="Актив")
    transaction_type = models.CharField(max_length=4, choices=TRANSACTION_TYPES, verbose_name="Тип угоди")
    quantity = models.DecimalField(max_digits=18, decimal_places=8, verbose_name="Кількість")
    execution_price = models.DecimalField(max_digits=18, decimal_places=8, verbose_name="Ціна за одиницю")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Час проведення угоди")

    class Meta:
        verbose_name = "Транзакція"
        verbose_name_plural = "Транзакції"

    def __str__(self) -> str:
        return f"{self.user.username} | {self.transaction_type} {self.quantity} {self.asset.ticker}"


class PortfolioPosition(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="positions", verbose_name="Користувач")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="positions", verbose_name="Актив")
    total_quantity = models.DecimalField(max_digits=18, decimal_places=8, default=0.0,
                                         verbose_name="Загальна кількість")
    average_buy_price = models.DecimalField(max_digits=18, decimal_places=8, default=0.0,
                                            verbose_name="Середня ціна купівлі")

    class Meta:
        verbose_name = "Позиція в портфелі"
        verbose_name_plural = "Позиції в портфелі"
        unique_together = ('user', 'asset')

    def __str__(self) -> str:
        return f"{self.user.username} | {self.asset.ticker}: {self.total_quantity}"