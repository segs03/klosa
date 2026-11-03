from django.urls import path
from .views import send_money_view, request_money

app_name = "transfers"

urlpatterns = [
    path(
        "send/",
        send_money_view,
        name="send"
    ),
    path("request/", request_money, name="request"),
]