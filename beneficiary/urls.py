
from django.urls import path
from . import views

app_name = "beneficiary"

urlpatterns = [
    path("", views.beneficiary_list, name="list"),
    path("add/", views.add_beneficiary, name="add"),
    path(
        "<int:pk>/delete/",
        views.delete_beneficiary,
        name="delete",
    ),
]
