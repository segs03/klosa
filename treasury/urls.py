from django.urls import path
from . import views

app_name = "treasury"

urlpatterns = [
    path("deposit/", views.deposit_info, name="deposit"),
    path("cashout/", views.cash_out, name="cashout"),
    path("teller/pay/", views.teller_pay, name="pay"),
    path("teller/cash-in/", views.teller_cash_in, name="cash_in"),
]