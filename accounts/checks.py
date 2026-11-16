from django.conf import settings

from django.core.checks import Error, Warning, register

TEST_PROVIDERS = ("BILLER_PROVIDER", "CASHIER_PROVIDER", "CARDS_ISSUER")
DEMO_SWITCHES = ("ALLOW_DEMO_CREDIT", "CASHIER_ALLOW_MOCK_PAYPAGE", "CARDS_ALLOW_SIMULATOR")


@register(deploy=True)
def no_test_features_in_production(app_configs, **kwargs):
    errors = []
    for name in TEST_PROVIDERS:
        if "mock" in str(getattr(settings, name, "")).lower():
            errors.append(Error(f"{name} is a test provider. Real money must never run through it.", id="klosa.E001"))
    for name in DEMO_SWITCHES:
        if getattr(settings, name, False):
            errors.append(Error(f"{name} is on. It is for demos only.", id="klosa.E002"))
    for name in ("SECRET_KEY", "IDENTITY_HASH_KEY"):
        if not getattr(settings, name, ""):
            errors.append(Error(f"{name} is empty.", id="klosa.E003"))
            
    if getattr(settings, "SMS_BACKEND", "console") == "console":
        errors.append(Error("SMS_BACKEND is the console. Customers will never receive codes.", id="klosa.E004"))
        
    if getattr(settings, "WELCOME_BONUS_ENABLED", False):
        errors.append(Warning("WELCOME_BONUS_ENABLED is on. Every bonus is money Klosa creates, so check the budget.", id="klosa.W001"))
        
    return errors