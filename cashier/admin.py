from django.contrib import admin
from .models import Deposit, Withdrawal


class ReadOnly(admin.ModelAdmin):
    search_fields = ("reference", "provider_reference", "account__account_number")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Deposit)
class DepositAdmin(ReadOnly):
    list_display = ("reference", "account", "method", "amount", "paid_amount", "status", "created_at")
    list_filter = ("status", "method")


@admin.register(Withdrawal)
class WithdrawalAdmin(ReadOnly):
    list_display = ("reference", "account", "bank_name", "amount", "status", "created_at")
    list_filter = ("status",)