from django.urls import path
from funding import views
from . import views_contrib

app_name = "funding"

urlpatterns = [
    path(
        "periods/",
        views.period_list_view,
        name="periods",
    ),
    path(
        "periods/<int:period_id>/reopen/",
        views.reopen_period_view,
        name="reopen_period",
    ),
    
    path("my-deposits/", views_contrib.my_deposits, name="my_deposits"),
    path("intents/", views_contrib.intents_queue, name="intents"),
    path("contributions/", views_contrib.contributions_queue, name="contributions"),
]