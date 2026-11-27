from django.urls import path

from . import views

app_name = "assetfund"

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.apply, name="apply"),
    path("staff/", views.staff_queue, name="staff"),
    path("staff/<int:pk>/", views.staff_detail, name="staff_detail"),
    path("<int:pk>/", views.detail, name="detail"),
]
