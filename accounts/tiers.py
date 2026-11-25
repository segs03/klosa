from django.conf import settings
from django.db import transaction

from .kyc_services import verified_types
from .models import Account, AddressProof

# BVN is compulsory. The upgrade needs one more ID from this set, plus an approved address.
REQUIRED_ID = "bvn"
SECOND_IDS = {"nin", "passport", "drivers_license"}

TIERS = [
    (1, "Phone verified", "Send, receive, save and pay"),
    (2, "BVN verified", "Higher limits, cash withdrawals, betting, physical and credit cards"),
    (3, "BVN, one more ID, your address and a face check", "The highest limits"),
]


def has_required_ids(types):
    """BVN plus at least one of NIN, passport or driver's licence."""
    return REQUIRED_ID in types and bool(set(types) & SECOND_IDS)


def address_ok(user):
    if not getattr(settings, "KYC_ADDRESS_REQUIRED", True):
        return True
    return AddressProof.objects.filter(user=user, status="approved").exists()


def earned(user, account):
    types = verified_types(user)
    if REQUIRED_ID not in types:
        return 0                                           # no BVN, no upgrade
    if has_required_ids(types) and account.face_verified_at and address_ok(user):
        return 2
    return 1


def refresh_tier(user):
    """Raises a tier when the customer qualifies. It never lowers one: that is a compliance decision."""
    with transaction.atomic():
        acc = Account.objects.select_for_update().filter(user=user).first()
        if acc is None or acc.closed_at:
            return None
        new = earned(user, acc)
        if new > acc.kyc_tier:
            acc.kyc_tier = new
            acc.save(update_fields=["kyc_tier"])       # update_fields lets the notice signal see the change
        return acc
