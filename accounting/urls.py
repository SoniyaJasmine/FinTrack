
from django.urls import path
from . import views

urlpatterns = [
    path('', views.transaction_list, name='transaction_list'),
    path('add/', views.add_transaction, name='add_transaction'),
    path('categories/add/', views.add_category, name='add_category'),
    path(
        'transactions/categorize/',
        views.categorize_transactions,
        name='categorize_transactions'
    ),
    path('gst-report/', views.generate_gst_report, name='generate_gst_report'),
    path(
        'transactions/<int:transaction_id>/gst-bill/',
        views.generate_gst_bill,
        name='generate_gst_bill'
    ),
    path(
        'analytics/',
        views.predictive_analytics,
        name='predictive_analytics'
    ),
]
