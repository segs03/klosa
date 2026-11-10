from django.urls import path
from funding import views

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
]