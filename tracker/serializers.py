from rest_framework import serializers
from .models import Transaction, Asset

class TransactionSerializer(serializers.ModelSerializer):
    # Добавляем строковое представление тикера (для удобства чтения на фронте)
    asset_ticker = serializers.ReadOnlyField(source='asset.ticker')

    class Meta:
        model = Transaction
        fields = ['id', 'asset', 'asset_ticker', 'transaction_type', 'quantity', 'execution_price', 'timestamp']
        read_only_fields = ['timestamp']

    def create(self, validated_data):
        # Автоматически привязываем сделку к тому юзеру, который отправил запрос
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)