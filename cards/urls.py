
from django.urls import path
from . import views

app_name = "cards"

urlpatterns = [
    # Card requests
    path("add/", views.add_card, name="add"),

    # Customer card-order records
    path("orders/", views.card_orders, name="orders"),

    path(
        "orders/<uuid:reference>/",
        views.card_order_detail,
        name="order_detail",
    ),
]
