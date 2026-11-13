
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import BankCard, CardOrder, CardDelivery


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
            last_four="4242",
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

        if order.card_type != "debit":
            raise CardIssuanceError(
                "Credit card issuance requires a separate eligibility process."
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
