from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("", views.home, name="home"),
    path("cards/", views.cards_queue, name="cards"),
    path("cards/<int:pk>/", views.card_detail, name="card_detail"),
    path("credit/", views.credit_queue, name="credit"),
    path("credit/<int:pk>/", views.credit_detail, name="credit_detail"),
    path("cash/", views.cash_queue, name="cash"),
    path("cash/<int:pk>/decide/", views.cash_decide, name="cash_decide"),
    path("customers/", views.customers, name="customers"),
    path("customers/<int:pk>/", views.customer_detail, name="customer"),
    path("operations/", views.operations, name="ops"),
    path("audit/", views.audit, name="audit"),    path("settlements/", views.settlements, name="settlements"),
]