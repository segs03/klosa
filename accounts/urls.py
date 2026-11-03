from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),

    path("credit/", views.credit_view, name="credit"),
    path("credit/apply/", views.credit_apply_view, name="credit_apply"),
    path("credit/repay/", views.credit_repay_view, name="credit_repay"),
]