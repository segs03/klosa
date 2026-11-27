from django.contrib import admin

from .models import AdvanceStage, AssetApplication, SaleRecord


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money moves only through the asset funding pages, so the rules and the ledger stay in step."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.concrete_fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StageInline(admin.TabularInline):
    model = AdvanceStage
    extra = 0
    can_delete = False
    fields = readonly_fields = ("order", "title", "amount", "status", "released_by", "released_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AssetApplication)
class AssetApplicationAdmin(ReadOnlyAdmin):
    list_display = ("pk", "title", "user", "kind", "requested_amount", "advance_amount", "status", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("title", "asset_ref", "user__username")
    inlines = [StageInline]


@admin.register(SaleRecord)
class SaleRecordAdmin(ReadOnlyAdmin):
    list_display = ("reference", "application", "proceeds", "status", "reported_at")
    list_filter = ("status",)
    search_fields = ("reference",)
