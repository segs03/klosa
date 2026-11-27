from django.contrib import admin

from funding.models import (
    CapitalContribution,
    ContributionIntent,
    FundingPeriod,
    FundingPeriodReopening,
    Investor,
    InvestorAgreement,
    InvestorAllocation,
    InvestorCapitalEntry,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)

from django.contrib import admin, messages

from backoffice.audit import log as audit_log




@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "user", "is_active", "created_at")
    search_fields = ("legal_name", "user__username", "user__phone_number")
    raw_id_fields = ("user",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InvestorAgreement)
class InvestorAgreementAdmin(admin.ModelAdmin):
    list_display = ("investor", "effective_from", "effective_to", "approved")
    raw_id_fields = ("investor",)
    actions = ["approve_agreements"]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.approved:
            return tuple(f.name for f in self.model._meta.fields)     # an approved agreement never changes
        return ("approved",)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve selected agreements (superuser only)")
    def approve_agreements(self, request, qs):
        if not request.user.is_superuser:
            self.message_user(request, "Only a superuser can approve agreements.", level=messages.ERROR)
            return
        n = 0
        for a in qs.filter(approved=False):
            a.full_clean()
            InvestorAgreement.objects.filter(pk=a.pk).update(approved=True)
            audit_log(request.user, "agreement_approved", f"agreement:{a.pk}", a.investor.legal_name)
            n += 1
        self.message_user(request, f"{n} agreement(s) approved.")




class ReadOnlyAdmin(admin.ModelAdmin):
    """Records the system writes itself: visible to staff, never edited by hand."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.concrete_fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FundingPeriod)
class FundingPeriodAdmin(admin.ModelAdmin):
    list_display = ("start_date", "end_date", "status", "closed_at")
    list_filter = ("status",)
    ordering = ("-start_date",)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            # New periods can be created, but always start open.
            return ("status", "closed_at", "created_at")
        # Existing periods are moved through review/close/reopen, not edited here.
        return tuple(field.name for field in self.model._meta.concrete_fields)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.status = "open"
            obj.closed_at = None
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FundingPeriodReopening)
class FundingPeriodReopeningAdmin(ReadOnlyAdmin):
    list_display = ("period", "reopened_by", "reason", "previous_closed_at", "reopened_at")
    list_filter = ("reopened_at",)
    search_fields = ("reason", "reopened_by__username")



@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "is_active")
    list_filter = ("account_type", "is_active")
    search_fields = ("code", "name")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CapitalContribution)
class CapitalContributionAdmin(admin.ModelAdmin):
    """Staff record a contribution here. A different person verifies it on the Verify page."""

    list_display = ("reference", "source_type", "investor", "amount", "received_at", "verified")
    list_filter = ("source_type", "verified")
    search_fields = ("reference", "investor__legal_name")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return tuple(f.name for f in self.model._meta.concrete_fields)
        return ("verified", "verified_by", "verified_at", "journal_reference", "recorded_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContributionIntent)
class ContributionIntentAdmin(ReadOnlyAdmin):
    list_display = ("reference", "investor", "amount", "method", "status", "created_at")
    list_filter = ("status", "method")
    search_fields = ("reference", "investor__legal_name")


@admin.register(InvestorAllocation)
class InvestorAllocationAdmin(ReadOnlyAdmin):
    list_display = ("period", "investor", "allocation_type", "amount", "approval_status")
    list_filter = ("allocation_type", "approval_status")


@admin.register(InvestorCapitalEntry)
class InvestorCapitalEntryAdmin(ReadOnlyAdmin):
    list_display = ("investor", "entry_type", "amount", "effective_date", "reference")
    list_filter = ("entry_type",)
    search_fields = ("reference", "investor__legal_name")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(ReadOnlyAdmin):
    list_display = ("reference", "period", "status", "posted_at")
    list_filter = ("status", "period")
    search_fields = ("reference", "description")
    inlines = [JournalLineInline]
