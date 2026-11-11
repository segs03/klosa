from django.urls import path
from . import views, passkey, onboarding, recovery

app_name = "accounts"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),

    path("credit/", views.credit_view, name="credit"),
    path("credit/apply/", views.credit_apply_view, name="credit_apply"),
    path("credit/repay/", views.credit_repay_view, name="credit_repay"),
    path(
    "credit/applications/",
    views.credit_applications_view,
    name="credit_applications",
),
path(
    "staff/credit/",
    views.credit_staff_dashboard_view,
    name="staff_credit_dashboard",
),

path(
    "staff/credit/<int:application_id>/assess/",
    views.assess_credit_application_view,
    name="assess_credit_application",
),

path(
    "staff/credit/<int:application_id>/decide/",
    views.decide_credit_application_view,
    name="decide_credit_application",
),
path("passkey/register/options/", passkey.passkey_register_options, name="passkey_register_options"),
path("passkey/register/verify/",  passkey.passkey_register_verify,  name="passkey_register_verify"),
path("passkey/login/options/",    passkey.passkey_login_options,    name="passkey_login_options"),
path("passkey/login/verify/",     passkey.passkey_login_verify,     name="passkey_login_verify"),
path("verify-phone/", onboarding.verify_phone_view, name="verify_phone"),

path("signup/", onboarding.signup_view, name="signup"),
path("pin/", views.set_pin_view, name="set_pin"),
path("staff/new/", views.staff_create_view, name="staff_create"),
path("password/change/", views.ForcedPasswordChangeView.as_view(), name="password_change"),

path("welcome/", onboarding.onboarding_view, name="onboarding"),
path("backup-codes/", onboarding.backup_codes_view, name="backup_codes"),
path("recover/", recovery.recover_request_view, name="recover"),
path("recover/verify/", recovery.recover_verify_view, name="recover_verify"),
path("close/", views.close_account_view, name="close_account"),
]