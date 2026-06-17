from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from decimal import Decimal
from .models import Transaction, PortfolioPosition


@receiver(post_save, sender=Transaction)
def update_portfolio_position(sender, instance, created, **kwargs):
    """
    Сигнал, который пересчитывает баланс пользователя (PortfolioPosition)
    каждый раз, когда создается новая сделка (Transaction).
    """
    if not created:
        return  # Реагируем только на создание новых сделок, а не на их обновление

    # transaction.atomic() гарантирует, что если что-то сломается, БД откатит изменения
    with transaction.atomic():
        # select_for_update() блокирует строку для других потоков на время расчета,
        # чтобы избежать бага двойного списания, если юзер быстро кликнет 2 раза
        position, pos_created = PortfolioPosition.objects.select_for_update().get_or_create(
            user=instance.user,
            asset=instance.asset,
            defaults={'total_quantity': Decimal('0.0'), 'average_buy_price': Decimal('0.0')}
        )

        if instance.transaction_type == 'BUY':
            # Считаем средневзвешенную цену покупки
            old_value = position.total_quantity * position.average_buy_price
            new_value = instance.quantity * instance.execution_price

            position.total_quantity += instance.quantity

            if position.total_quantity > Decimal('0.0'):
                position.average_buy_price = (old_value + new_value) / position.total_quantity

            position.save()

        elif instance.transaction_type == 'SELL':
            position.total_quantity -= instance.quantity

            # Если юзер продал всё (или ушел в минус из-за ошибки округления), удаляем позицию
            if position.total_quantity <= Decimal('0.0'):
                position.delete()
            else:
                position.save()