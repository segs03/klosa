
from django.urls import path
from . import views
from . import ordering, webhooks

    

app_name = "cards"

urlpatterns = [

    path("add/", ordering.order_card, name="add"),            # replaces your old add view
    path("simulate/", ordering.simulate_purchase, name="simulate"),
    path("webhook/", webhooks.webhook, name="webhook"),

    # Card requests
    # path("add/", views.add_card, name="add"),

    # Customer card-order records
   # path("orders/", views.card_orders, name="orders"),

  #  path( "orders/<uuid:reference>/",views.card_order_detail,name="order_detail", ),
]
