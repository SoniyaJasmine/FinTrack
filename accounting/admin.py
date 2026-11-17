
from django.contrib import admin
from .models import Transaction, Gst, Ledger


admin.site.register(Transaction)
admin.site.register(Gst)
admin.site.register(Ledger)
