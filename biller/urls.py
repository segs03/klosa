from django.urls import path
from . import views

app_name = "biller"

urlpatterns = [
    path("", views.hub, name="hub"),
    path("history/", views.history, name="history"),
    path("order/<str:reference>/", views.order_detail, name="order"),
    path("order/<str:reference>/check/", views.order_check, name="check"),
    path("<slug:category>/", views.service, name="service"),
]