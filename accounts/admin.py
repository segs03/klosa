from django.contrib import admin
from .models import CreditPolicy,CreditApplication



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
    
    
