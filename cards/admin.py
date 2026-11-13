
from .models import (
    BankCard,
    CardOrder,
    CardDelivery,
    CardTransaction,
    CardProviderEvent,
)

from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse

from .services import (
    approve_physical_card_order,
    reject_physical_card_order,
    CardIssuanceError,
)


@admin.register(BankCard)
class BankCardAdmin(admin.ModelAdmin):
    list_display = (
        "cardholder_name",
        "account",
        "card_type",
        "last_four",
        "status",
        "online_enabled",
        "created_at",
    )

    list_filter = (
        "card_type",
        "status",
        "online_enabled",
    )

    search_fields = (
        "cardholder_name",
        "last_four",
        "account__account_number",
    )

    readonly_fields = (
        "account",
        "card_type",
        "cardholder_name",
        "last_four",
        "expiry_month",
        "expiry_year",
        "status",
        "provider_name",
        "provider_card_id",
        "provider_token",
        "online_enabled",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



@admin.register(CardOrder)
class CardOrderAdmin(admin.ModelAdmin):

    list_display = (
        "reference",
        "account",
        "card_type",
        "card_format",
        "status",
        "reviewed_by",
        "created_at",
    )

    list_filter = (
        "card_type",
        "card_format",
        "status",
        "created_at",
    )

    search_fields = (
        "reference",
        "account__account_number",
        "cardholder_name",
    )

    readonly_fields = (
        "reference",
        "account",
        "card_type",
        "card_format",
        "cardholder_name",
        "delivery_address",
        "delivery_city",
        "delivery_state",
        "delivery_phone",
        "status",
        "bank_card",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "created_at",
        "updated_at",
    )

    actions = ["approve_physical_orders"]
    
    @admin.action(description="Approve selected physical card orders", permissions=["approve_orders"],)
    def approve_physical_orders(self, request, queryset):
        from .services import (
        approve_physical_card_order,
        CardIssuanceError,
    )

        for order in queryset:
            try:
                approve_physical_card_order(order.pk, request.user)
            except CardIssuanceError as exc:
                self.message_user(
                request,
                f"Order {order.pk}: {exc}",
                level="error",
            )

    def has_approve_orders_permission(self, request):
        return request.user.has_perm("cards.approve_cardorder")

    @admin.action(description="Approve and issue selected physical cards")
    def approve_physical_orders(self, request, queryset):

        if not request.user.is_staff:
            self.message_user(
                request,
                "You are not authorized.",
                messages.ERROR,
            )
            return

        success_count = 0
        errors = []

        for order in queryset:
            try:
                approve_physical_card_order(
                    order.pk,
                    request.user,
                )
                success_count += 1

            except CardIssuanceError as exc:
                errors.append(
                    f"{order.reference}: {exc}"
                )

        if success_count:
            self.message_user(
                request,
                f"{success_count} physical card order(s) issued.",
                messages.SUCCESS,
            )

        for error in errors:
            self.message_user(
                request,
                error,
                messages.ERROR,
            )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff


@admin.register(CardDelivery)
class CardDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "courier",
        "tracking_number",
        "status",
        "dispatched_at",
        "delivered_at",
    )

    list_filter = ("status", "courier")

    search_fields = (
        "tracking_number",
        "order__reference",
    )

    readonly_fields = ("updated_at",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CardTransaction)
class CardTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "card",
        "merchant_name",
        "transaction_type",
        "amount",
        "currency",
        "status",
        "occurred_at",
    )

    list_filter = (
        "transaction_type",
        "status",
        "currency",
        "occurred_at",
    )

    search_fields = (
        "merchant_name",
        "provider_transaction_id",
        "card__last_four",
    )

    readonly_fields = (
        "card",
        "transaction_type",
        "status",
        "merchant_name",
        "merchant_reference",
        "amount",
        "currency",
        "provider_transaction_id",
        "occurred_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CardProviderEvent)
class CardProviderEventAdmin(admin.ModelAdmin):
    list_display = (
        "provider_name",
        "event_id",
        "event_type",
        "processed",
        "received_at",
    )

    list_filter = ("provider_name", "processed", "event_type")

    search_fields = ("event_id", "event_type")

    readonly_fields = (
        "provider_name",
        "event_id",
        "event_type",
        "processed",
        "received_at",
        "processed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
