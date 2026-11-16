
import secrets
from dataclasses import dataclass
from datetime import date

import logging
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from accounts.models import Account, CreditAccount, VerifiedIdentity
from accounts.utils import normalize_phone
from .models import BankCard, CardDelivery, CardOrder, CardTransaction




log = logging.getLogger(__name__)
OPEN = ("pending", "approved", "issuing")
MAX_CARDS = 3
NAME_RE = re.compile(r"^[A-Z][A-Z .'\-]{1,25}$")


def card_name(legal):
    name = " ".join(legal.upper().split())
    if len(name) > 26 and len(name.split()) > 1:
        parts = name.split()
        name = f"{parts[0]} {parts[-1]}"                      # drop middle names to fit the card
    return name


def place_order(account, *, card_type, card_format, cardholder_name="", address="", city="", state="", phone=""):
    if card_type not in ("debit", "credit") or card_format not in ("physical", "virtual"):
        raise ValidationError("Choose a card type and format.")
    with transaction.atomic():
        acc = Account.objects.select_for_update().get(pk=account.pk)     # one order at a time per account
        if acc.closed_at:
            raise ValidationError("Your account is closed.")
        ident = VerifiedIdentity.objects.select_related("source_application").filter(user_id=acc.user_id).first()
        if (card_format == "physical" or card_type == "credit") and ident is None:
            raise ValidationError("Verify your ID to order this card.")
        if card_type == "credit" and not CreditAccount.objects.filter(account=acc, status="active").exists():
            raise ValidationError("You need an approved credit facility first.")

        name = card_name(ident.source_application.legal_name if ident else cardholder_name or "")
        if not NAME_RE.fullmatch(name):
            raise ValidationError("Enter the name as it should appear on the card: letters only, up to 26 characters.")

        live = BankCard.objects.filter(account=acc).exclude(status__in=["cancelled", "expired"])
        open_orders = CardOrder.objects.filter(account=acc, status__in=OPEN)
        if live.count() + open_orders.count() >= MAX_CARDS:
            raise ValidationError(f"You can have up to {MAX_CARDS} cards.")
        if card_type == "credit":
            if live.filter(card_type="credit").exists() or open_orders.filter(card_type="credit").exists():
                raise ValidationError("You already have a credit card or a pending credit card request.")
        elif open_orders.filter(card_type=card_type, card_format=card_format).exists():
            raise ValidationError("You already have a pending request for this card.")

        extra = {}
        if card_format == "physical":
            address, city, state = ((x or "").strip() for x in (address, city, state))
            address, city, state = (" ".join(x.split()) for x in (address, city, state))
            phone_n = normalize_phone(phone)
            if not (10 <= len(address) <= 250 and 2 <= len(city) <= 100 and 2 <= len(state) <= 100):
                raise ValidationError("Enter your full delivery address, city and state.")
            if not phone_n:
                raise ValidationError("Enter a valid phone number for the courier.")
            extra = dict(delivery_address=address, delivery_city=city, delivery_state=state, delivery_phone=phone_n)

        order = CardOrder.objects.create(account=acc, card_type=card_type, card_format=card_format,
                                         cardholder_name=name, **extra)
        if card_format == "physical":
            CardDelivery.objects.create(order=order)
        return order


def _staff(user):
    if not (user and user.is_authenticated and user.is_active and user.is_staff
            and user.has_perm("cards.approve_cardorder")):
        raise PermissionDenied("You are not allowed to review card requests.")


@transaction.atomic
def approve_order(order_id, reviewer):
    _staff(reviewer)
    order = CardOrder.objects.select_for_update(of=("self",)).select_related("account").get(pk=order_id)
    if order.status != "pending":
        raise ValidationError("This request is not waiting for review.")
    if order.account.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own card request.")
    if order.card_type == "credit" and not CreditAccount.objects.filter(account=order.account, status="active").exists():
        raise ValidationError("The credit facility is no longer active.")
    order.status, order.reviewed_by, order.reviewed_at = "approved", reviewer, timezone.now()
    order.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return order


@transaction.atomic
def reject_order(order_id, reviewer, reason):
    _staff(reviewer)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required.")
    order = CardOrder.objects.select_for_update(of=("self",)).select_related("account").get(pk=order_id)
    if order.status != "pending":
        raise ValidationError("This request is not waiting for review.")
    if order.account.user_id == reviewer.pk:
        raise PermissionDenied("You cannot review your own card request.")
    order.status, order.reviewed_by, order.reviewed_at = "rejected", reviewer, timezone.now()
    order.rejection_reason = reason[:500]
    order.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    return order


def issue_order(order_id):
    """Approved (or failed) order to a live card. The issuer is called outside every lock."""
    with transaction.atomic():
        order = CardOrder.objects.select_for_update(of=("self",)).get(pk=order_id)
        if order.status not in ("approved", "failed"):
            raise ValidationError("Only approved requests can be issued.")
        order.status = "issuing"
        order.save(update_fields=["status", "updated_at"])
    try:
        details = import_string(settings.CARDS_ISSUER)().issue(order)
    except Exception:
        log.exception("Card issuer failed for order %s", order_id)
        CardOrder.objects.filter(pk=order_id, status="issuing").update(status="failed")
        raise ValidationError("The card issuer could not create this card. You can try again.")
    with transaction.atomic():
        order = CardOrder.objects.select_for_update(of=("self",)).get(pk=order_id)
        if order.status != "issuing":
            return order
        card = BankCard.objects.create(account_id=order.account_id, card_type=order.card_type,
                                       cardholder_name=order.cardholder_name, status="active", **details)
        order.bank_card, order.status = card, "issued"
        order.save(update_fields=["bank_card", "status", "updated_at"])
        return order


# ------------------------------------------------------------ card payments
def authorize_purchase(card, amount, tx_id, merchant="", channel="pos", currency="NGN"):
    """Decide one card payment. Returns (approved, reason). Safe to call again with the same tx_id."""
    from accounts.credit_services import spend_on_credit
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError):
        return False, "Invalid amount"
    if currency != "NGN":
        return False, "Currency not supported"
    if card.card_type != "credit":
        return False, "Debit card payments are not enabled yet"
    if channel == "online" and not card.online_enabled:
        return False, "Online payments are switched off"
    merchant = (merchant or "Merchant")[:150]
    try:
        spend_on_credit(card, amount, reference=tx_id, merchant=merchant)
        approved, reason = True, ""
    except ValidationError as e:
        approved, reason = False, e.messages[0]
    row, created = CardTransaction.objects.get_or_create(
        provider_transaction_id=tx_id,
        defaults=dict(card=card, transaction_type="purchase", merchant_name=merchant, amount=amount,
                      currency=currency, occurred_at=timezone.now(),
                      status="authorized" if approved else "declined"))
    if not created and approved and row.status in ("declined", "pending"):
        row.status = "authorized"
        row.save(update_fields=["status"])
    return approved, reason


def refund_purchase(original_tx_id, amount=None):
    from accounts.credit_services import refund_credit_purchase
    row = CardTransaction.objects.filter(provider_transaction_id=original_tx_id, transaction_type="purchase").first()
    if row is None or row.status not in ("authorized", "settled", "reversed"):
        return False
    refund_credit_purchase(original_tx_id, amount)
    if amount is None or Decimal(str(amount)) == row.amount:
        row.status = "reversed"
        row.save(update_fields=["status"])
    return True








class CardIssuanceError(Exception):
    pass


@dataclass(frozen=True)
class IssuedCardData:
    provider_name: str
    provider_card_id: str
    last_four: str
    expiry_month: int
    expiry_year: int
    online_enabled: bool
    status: str = "active"


class SandboxCardIssuer:
    """
    Development-only card issuer.

    Does not create real payment cards or enable real purchases.
    """

    provider_name = "klosa_sandbox"

    def issue(self, order, idempotency_key):
        today = timezone.localdate()

        return IssuedCardData(
    provider_name=self.provider_name,
    provider_card_id=f"sandbox-{order.reference}",
    last_four=str(
        secrets.randbelow(10000)
    ).zfill(4),
    expiry_month=today.month,
    expiry_year=today.year + 5,
    online_enabled=True,
    status="active",
)


def _has_approval_permission(user):
    return bool(
        user
        and user.is_authenticated
        and user.has_perm("cards.approve_cardorder")
    )


def _validate_credit_card_eligibility(order):
    try:
        credit_account = CreditAccount.objects.get(
            account=order.account
        )
    except CreditAccount.DoesNotExist:
        raise CardIssuanceError(
            "No approved credit facility exists."
        )

    if credit_account.status != "active":
        raise CardIssuanceError(
            "The credit facility is not active."
        )

    if credit_account.credit_limit <= 0:
        raise CardIssuanceError(
            "No usable credit limit exists."
        )

    return credit_account



def _claim_order(order_id, expected_format, reviewer=None):
    with transaction.atomic():
        order = (
            CardOrder.objects
            .select_for_update(of=("self",))
            .get(pk=order_id)
        )

        if order.card_format != expected_format:
            raise CardIssuanceError("Incorrect card format.")

        if order.bank_card_id:
            return order, True

        if order.status == "issued":
            raise CardIssuanceError(
                "Order is marked issued but has no linked card. "
                "Investigate this record."
            )

        if order.card_type == "credit":
            from accounts.models import CreditAccount

            try:
                credit_account = CreditAccount.objects.get(
                    account=order.account
                )
            except CreditAccount.DoesNotExist:
                raise CardIssuanceError(
                    "No approved credit facility exists."
                )

            if credit_account.status != "active":
                raise CardIssuanceError(
                    "The credit facility is not active."
                )

            if credit_account.credit_limit <= 0:
                raise CardIssuanceError(
                    "No approved credit limit exists."
                )

            existing_card = BankCard.objects.filter(
                account=order.account,
                card_type="credit",
                status="active",
            ).exists()

            if existing_card:
                raise CardIssuanceError(
                    "An active credit card already exists."
                )


        if expected_format == "physical":
            if not _has_approval_permission(reviewer):
                raise CardIssuanceError(
                    "You are not authorised to approve card orders."
                )

            if order.status == "pending":
                order.reviewed_by = reviewer
                order.reviewed_at = timezone.now()
                order.status = "approved"

            elif order.status != "approved":
                raise CardIssuanceError(
                    "This physical order is not awaiting issuance."
                )

        elif expected_format == "virtual":
            if order.status not in ("pending", "failed"):
                raise CardIssuanceError(
                    "This virtual order cannot be issued in its current state."
                )

        order.status = "issuing"
        order.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

        return order, False


def _validate_issuer_response(data):
    if not data.provider_name or not data.provider_card_id:
        raise CardIssuanceError("Issuer response is missing its reference.")

    if not data.last_four.isdigit() or len(data.last_four) != 4:
        raise CardIssuanceError("Issuer returned invalid card digits.")

    if not 1 <= data.expiry_month <= 12:
        raise CardIssuanceError("Issuer returned an invalid expiry month.")

    today = timezone.localdate()

    if (
        data.expiry_year < today.year
        or (
            data.expiry_year == today.year
            and data.expiry_month < today.month
        )
    ):
        raise CardIssuanceError("Issuer returned an expired card.")

    if data.status not in ("active", "blocked"):
        raise CardIssuanceError("Issuer returned an unsupported card status.")


def _complete_issuance(order_id, issued_data):
    _validate_issuer_response(issued_data)

    with transaction.atomic():
        order = (
            CardOrder.objects
            .select_for_update(of=("self",))
            .get(pk=order_id)
        )

        if order.bank_card_id:
            return order.bank_card

        if order.status != "issuing":
            raise CardIssuanceError(
                "Order is no longer awaiting issuance."
            )
          
        if BankCard.objects.filter(
            provider_card_id=issued_data.provider_card_id
        ).exists():
            raise CardIssuanceError(
                "A card already exists for this issuer reference."
            )


        card = BankCard.objects.create(
            account=order.account,
            card_type=order.card_type,
            cardholder_name=order.cardholder_name,
            last_four=issued_data.last_four,
            expiry_month=issued_data.expiry_month,
            expiry_year=issued_data.expiry_year,
            status=issued_data.status,
            provider_name=issued_data.provider_name,
            provider_card_id=issued_data.provider_card_id,
            online_enabled=issued_data.online_enabled,
        )

        order.bank_card = card
        order.status = "issued"
        order.save(
            update_fields=["bank_card", "status", "updated_at"]
        )

        if order.card_format == "physical":
            CardDelivery.objects.get_or_create(order=order)

        return card


def issue_credit_virtual_card(account):
    order = CardOrder.objects.create(
        account=account,
        card_type="credit",
        card_format="virtual",
        cardholder_name=(
            account.user.get_full_name()
            or account.user.username
        ),
    )

    return issue_virtual_card_order(order.pk)



def _issue_order(order_id, expected_format, reviewer=None):
    order, already_issued = _claim_order(
        order_id,
        expected_format,
        reviewer,
    )

    if already_issued:
        return order.bank_card

    issuer = SandboxCardIssuer()

    try:
        issued_data = issuer.issue(
            order,
            idempotency_key=str(order.reference),
        )

        return _complete_issuance(order.pk, issued_data)

    except Exception as exc:
        # Do not automatically mark an order failed.
        # A real issuer timeout may mean the card was already created.
        # Reconcile with the issuer using the same idempotency key.
        raise CardIssuanceError(
            "Issuance could not be confirmed. "
            "Check the issuer using the order reference before retrying."
        ) from exc


def approve_physical_card_order(order_id, staff_user):
    return _issue_order(
        order_id,
        expected_format="physical",
        reviewer=staff_user,
    )


def issue_virtual_card_order(order_id):
    return _issue_order(
        order_id,
        expected_format="virtual",
    )


def reject_physical_card_order(order_id, staff_user, reason):
    if not _has_approval_permission(staff_user):
        raise CardIssuanceError(
            "You are not authorised to reject card orders."
        )

    reason = (reason or "").strip()

    if not reason:
        raise CardIssuanceError("A rejection reason is required.")

    with transaction.atomic():
        order = (
            CardOrder.objects
            .select_for_update(of=("self",))
            .get(pk=order_id)
        )

        if order.card_format != "physical":
            raise CardIssuanceError(
                "Only physical card orders can be rejected."
            )

        if order.status != "pending":
            raise CardIssuanceError(
                "Only pending orders can be rejected."
            )

        order.status = "rejected"
        order.reviewed_by = staff_user
        order.reviewed_at = timezone.now()
        order.rejection_reason = reason

        order.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        return order


def update_card_delivery(order_id, new_status, tracking_number=None, courier=None):
    """
    Update delivery information for an issued physical card.
    """

    allowed_transitions = {
        "not_dispatched": {"dispatched"},
        "dispatched": {"in_transit", "delivery_failed"},
        "in_transit": {"delivered", "delivery_failed"},
        "delivery_failed": set(),
        "delivered": set(),
    }

    with transaction.atomic():
        order = (
            CardOrder.objects
            .select_for_update(of=("self",))
            .get(pk=order_id)
        )

        if order.card_format != "physical" or order.status != "issued":
            raise CardIssuanceError(
                "Delivery tracking requires an issued physical card."
            )

        delivery = (
            CardDelivery.objects
            .select_for_update(of=("self",))
            .filter(order=order)
            .first()
        )

        if not delivery:
            raise CardIssuanceError(
                "Delivery record does not exist."
            )

        if new_status not in allowed_transitions.get(delivery.status, set()):
            raise CardIssuanceError(
                f"Cannot change delivery from {delivery.status} "
                f"to {new_status}."
            )

        if tracking_number is not None:
            delivery.tracking_number = tracking_number.strip()

        if courier is not None:
            delivery.courier = courier.strip()

        delivery.status = new_status

        if new_status == "dispatched":
            delivery.dispatched_at = timezone.now()

        elif new_status == "delivered":
            delivery.delivered_at = timezone.now()

        delivery.save()

        return delivery
        
        
