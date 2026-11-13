from django.urls import path
from . import views

app_name = "savings"

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.new_plan, name="new"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/add/", views.add_money, name="deposit"),
    path("<int:pk>/withdraw/", views.take_out, name="withdraw"),
    path("<int:pk>/close/", views.close_plan, name="close"),
]