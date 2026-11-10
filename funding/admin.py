from django.contrib import admin

from funding.models import (
    FundingPeriod,
    FundingPeriodReopening,
)


@admin.register(FundingPeriod)
class FundingPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "start_date",
        "end_date",
        "status",
        "closed_at",
    )
    list_filter = ("status",)
    ordering = ("-start_date",)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            # New periods can be created, but always start open.
            return ("status", "closed_at", "created_at")

        # Existing periods cannot be edited through Admin.
        return tuple(
            field.name
            for field in self.model._meta.concrete_fields
        )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.status = "open"
            obj.closed_at = None

        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FundingPeriodReopening)
class FundingPeriodReopeningAdmin(admin.ModelAdmin):
    list_display = (
        "period",
        "reopened_by",
        "reason",
        "previous_closed_at",
        "reopened_at",
    )
    list_filter = ("reopened_at",)
    search_fields = (
        "reason",
        "reopened_by__username",
    )
    readonly_fields = tuple(
        field.name
        for field in FundingPeriodReopening._meta.concrete_fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False