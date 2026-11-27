import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .accounting import add_journal_line, post_journal_entry
from .models import FundingPeriod, JournalEntry, LedgerPosting

log = logging.getLogger(__name__)


def _sources():
    """(kind, rows, date, amount, debit, credit, text). Transfers between customers post nothing: they only move
    what Klosa already owes. Credit lines are a receivable and are not posted yet."""
    from accounts.models import WelcomeBonus
    from biller.models import BillPayment
    from cashier.models import Deposit, Withdrawal
    from savings.models import SavingsEntry
    from treasury.models import CashOut, CashRequest
    owe = "customer_deposits"
    return [
        ("cashin", CashRequest.objects.filter(status="posted"), lambda r: r.decided_at, lambda r: r.amount, "cash", owe, "Cash deposit at a branch"),
        ("cashout", CashOut.objects.filter(status="paid"), lambda r: r.paid_at, lambda r: r.amount, owe, "cash", "Cash paid out at a branch"),
        ("gateway_in", Deposit.objects.filter(status="completed"), lambda r: r.completed_at, lambda r: r.paid_amount, "cash", owe, "Deposit through a payment company"),
        ("gateway_out", Withdrawal.objects.filter(status="completed"), lambda r: r.completed_at, lambda r: r.amount + r.fee, owe, "cash", "Withdrawal to another bank"),
        ("bill", BillPayment.objects.filter(status="completed"), lambda r: r.completed_at, lambda r: r.amount + r.fee, owe, "cash", "Bill payment settled"),
        ("bonus", WelcomeBonus.objects.all(), lambda r: r.granted_at, lambda r: r.amount, "bonus_expense", owe, "Welcome bonus"),
        ("interest", SavingsEntry.objects.filter(entry_type="interest"), lambda r: r.created_at, lambda r: r.amount, "interest_expense", owe, "Savings interest"),
    ]


def post_event(kind, source_id, when, amount, debit, credit, text):
    """'posted', 'done' (already there), 'skip' (nothing to post) or 'no_period' (no open accounting period)."""
    if not amount or amount <= 0:
        return "skip"
    day = timezone.localtime(when).date()
    periods = list(FundingPeriod.objects.filter(start_date__lte=day, end_date__gte=day, status="open"))
    if len(periods) != 1:
        return "no_period"
    try:
        with transaction.atomic():                       # the marker and the journal succeed or fail together
            posting = LedgerPosting.objects.create(source_type=kind, source_id=source_id)
            journal = JournalEntry.objects.create(reference=f"AUTO-{kind}-{source_id}", period=periods[0],
                                                  description=text[:255], status="draft")
            add_journal_line(journal, debit, amount, 0, text)
            add_journal_line(journal, credit, 0, amount, text)
            post_journal_entry(journal)
            posting.entry = journal
            posting.save(update_fields=["entry"])
    except IntegrityError:
        return "done"
    except ValidationError as error:
        # e.g. a ledger account is missing. Leave the row unposted so the next run retries it.
        log.error("Could not post %s %s: %s", kind, source_id, "; ".join(error.messages))
        return "failed"
    return "posted"


def post_pending(limit=500):
    posted = skipped = 0
    failed = 0
    for kind, rows, when, amount, debit, credit, text in _sources():
        done = LedgerPosting.objects.filter(source_type=kind).values("source_id")
        for row in rows.exclude(pk__in=done).order_by("pk")[:limit]:
            result = post_event(kind, row.pk, when(row) or timezone.now(), amount(row), debit, credit, text)
            posted += result == "posted"
            skipped += result == "no_period"
            failed += result == "failed"
    if failed:
        log.error("%s ledger postings failed; see the errors above.", failed)
    return posted, skipped

