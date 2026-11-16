from django.urls import path

from . import views

app_name = "notices"

urlpatterns = [
    path("", views.inbox, name="list"),
    path("read-all/", views.read_all, name="read_all"),
    path("<int:pk>/open/", views.open_notice, name="open"),
]