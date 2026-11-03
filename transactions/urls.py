from django.urls import path
from . import views

app_name = "transactions"

urlpatterns = [
    path("", views.transaction_list, name="list"),
path("receipt/<str:reference>/", views.receipt, name="receipt"),
path(
        "credit/transactions/",
        views.credit_transactions_view,
        name="credit_transactions",
    ),
]