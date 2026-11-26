from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .kyc_services import verified_types
from .models import Account
from .services import TIER_LIMITS
from .tiers import SECOND_IDS, TIERS, address_ok, email_ok, refresh_tier


@login_required
def upgrade(request):
    acc = get_object_or_404(Account, user=request.user)
    types = verified_types(request.user)
    has_bvn = "bvn" in types
    email = email_ok(request.user)                     # always True while the email rule is switched off
    has_second = bool(set(types) & SECOND_IDS)         # NIN, passport or driver's licence
    has_address = address_ok(request.user)             # always True while the address rule is switched off
    face = bool(acc.face_verified_at)

    # The next thing to do, in the order the customer should do it:
    # tier 2 needs BVN + email, tier 3 adds a second ID, an address and the face check.
    if not has_bvn:
        nxt = ("Verify your BVN", reverse("accounts:verify"))
    elif not email:
        nxt = ("Verify your email", reverse("accounts:email"))
    elif not has_second:
        nxt = ("Verify one more ID (NIN, passport or licence)", reverse("accounts:verify"))
    elif not has_address:
        nxt = ("Add your address", reverse("accounts:address"))
    elif not face:
        nxt = ("Do the face check", reverse("face_auth:begin", args=["upgrade"]))
    else:
        nxt = None

    rows = [{"n": n, "need": need, "gives": gives, "single": TIER_LIMITS[n - 1]["single"],
             "daily": TIER_LIMITS[n - 1]["daily"], "reached": acc.tier >= n, "current": acc.tier == n}
            for n, need, gives in TIERS]
    checks = [("Phone", True), ("BVN", has_bvn), ("Email", email),
              ("One more ID", has_second), ("Address", has_address), ("Face check", face)]
    return render(request, "accounts/upgrade.html", {"acc": acc, "rows": rows, "nxt": nxt, "checks": checks})


@login_required
@require_POST
def recheck(request):
    acc = refresh_tier(request.user)
    if acc:
        messages.success(request, f"You are on Tier {acc.tier}.")
    return redirect("accounts:upgrade")