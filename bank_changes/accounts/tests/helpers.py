import uuid
from datetime import date
from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from accounts.models import Account, KYCApplication, VerifiedDocument

User = get_user_model()
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PASSWORD = "pass12345"
_seq = count(1)


def make_user(username=None, **extra):
    n = next(_seq)
    return User.objects.create_user(username or f"user{n}", password=PASSWORD, **extra)


def make_account(user, balance="0", **extra):
    n = next(_seq)
    return Account.objects.create(user=user, account_number=f"3{n:09d}", balance=Decimal(balance), **extra)


def make_verified(user, doc_type, name="Ada Obi", dob=date(1990, 5, 17)):
    digest = uuid.uuid4().hex + uuid.uuid4().hex
    app = KYCApplication.objects.create(
        user=user, country_code="NG", document_type=doc_type, legal_name=name,
        date_of_birth=dob, identity_digest=digest, status="verified")
    VerifiedDocument.objects.create(
        user=user, document_type=doc_type, identity_digest=digest,
        verified_at=timezone.now(), source_application=app)
    return app


def make_staff(codename, username=None):
    user = make_user(username, is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename=codename))
    return user
