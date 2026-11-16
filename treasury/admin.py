from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

from .models import CashOut, CashRequest
from .services import TreasuryError, approve_cash_in, reject_cash_in


class ReadOnly(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashRequest)
class CashRequestAdmin(ReadOnly):
    list_display = ("reference", "account", "amount", "branch", "status", "maker", "created_at")
    list_filter = ("status", "branch")
    search_fields = ("reference", "account__account_number")
    actions = ["approve", "reject"]

    def _each(self, request, qs, fn, done):
        ok = 0
        for r in qs:
            try:
                fn(r)
                ok += 1
            except (TreasuryError, PermissionDenied) as e:
                self.message_user(request, f"{r.reference}: {e or 'Not allowed.'}", level=messages.ERROR)
        if ok:
            self.message_user(request, f"{ok} request(s) {done}.")

    @admin.action(description="Approve and credit the customer")
    def approve(self, request, qs):
        self._each(request, qs, lambda r: approve_cash_in(r.pk, request.user), "approved")

    @admin.action(description="Reject")
    def reject(self, request, qs):
        self._each(request, qs, lambda r: reject_cash_in(r.pk, request.user, "Rejected by approver"), "rejected")


@admin.register(CashOut)
class CashOutAdmin(ReadOnly):
    list_display = ("reference", "account", "amount", "status", "expires_at", "paid_by")
    list_filter = ("status",)