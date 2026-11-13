from django.urls import path
from . import views

app_name = "transfers"

urlpatterns = [
    path("send/", views.send_money_view, name="send"),
    path("request/", views.request_money, name="request_money"),
    path("requests/", views.requests_inbox, name="requests"),
    path("requests/<int:pk>/pay/", views.pay_request, name="pay_request"),
    path("requests/<int:pk>/decline/", views.decline_request, name="decline_request"),
    path("requests/<int:pk>/cancel/", views.cancel_request, name="cancel_request"),
]