from django.urls import path
from . import views

app_name = "cashier"

urlpatterns = [
    path("deposit/", views.deposit, name="deposit"),
    path("deposit/<str:reference>/", views.deposit_status, name="deposit_status"),
    path("deposit/<str:reference>/poll/", views.deposit_poll, name="deposit_poll"),
    path("deposit/<str:reference>/mock/", views.mock_pay, name="mockpay"),
    path("withdraw/", views.withdraw, name="withdraw"),
    path("withdraw/<str:reference>/", views.withdraw_order, name="withdraw_order"),
    path("withdraw/<str:reference>/poll/", views.withdraw_poll, name="withdraw_poll"),
    path("webhook/", views.webhook, name="webhook"),
    
    # PAYSTACK WEBHOOK
    path("webhook/paystack/", views.paystack_webhook, name="paystack_webhook")
]