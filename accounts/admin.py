
from .models import CreditPolicy,CreditApplication, User

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.utils.crypto import get_random_string


@admin.register(User)
class BankUserAdmin(UserAdmin):
    list_display = ("username", "phone_number", "email", "is_staff", "is_active")
    search_fields = ("username", "phone_number", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Bank", {"fields": ("phone_number", "phone_verified", "email_verified", "must_change_password")}),)
    actions = ["reset_staff_password"]

    @admin.action(description="Reset staff password (shows a temporary one)")
    def reset_staff_password(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "Superusers only.", level=messages.ERROR)
            return
        for u in queryset.filter(is_staff=True, is_superuser=False):
            temp = get_random_string(12)
            u.set_password(temp)
            u.must_change_password = True
            u.save()
            self.message_user(request, f"{u.email}: {temp}")



@admin.register(CreditApplication)
class CreditApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "requested_amount",
        "assessed_amount",
        "status",
        "submitted_at",
    )

    list_filter = ("status", "submitted_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = (
        "submitted_at",
        "assessed_at",
        "decided_at",
    )

    ordering = ("-submitted_at",)


@admin.register(CreditPolicy)
class CreditPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "account_level",
        "currency",
        "minimum_limit",
        "maximum_limit",
        "is_active",
    )
    list_filter = ("account_level", "currency", "is_active")
    
    
