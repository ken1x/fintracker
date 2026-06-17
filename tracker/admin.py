from django.contrib import admin
from .models import Asset, AssetPriceHistory, Transaction, PortfolioPosition

admin.site.register(Asset)
admin.site.register(AssetPriceHistory)
admin.site.register(Transaction)
admin.site.register(PortfolioPosition)