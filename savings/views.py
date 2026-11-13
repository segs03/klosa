import uuid
from decimal import ROUND_DOWN, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Account
from . import rules
from .forms import CreatePlanForm, MoveForm, PinForm
from .models import SavingsPlan
from .services import SavingsError, break_locked, close_flex, create_plan, deposit, withdraw


def _ref(request):
    return request.session.setdefault("sv_ref", "SV" + uuid.uuid4().hex[:10].upper())


def _account(request):
    return get_object_or_404(Account, user=request.user)


def _plan(acc, pk):
    return get_object_or_404(SavingsPlan, pk=pk, account=acc)


def _page(request, title, form, button, error=""):
    return render(request, "accounts/form_page.html",
                  {"title": title, "form": form, "button": button, "error": error})


def _fail(form, e):
    msg = str(e)
    field = "pin" if ("PIN" in msg or "locked" in msg) else ("amount" if "amount" in form.fields else None)
    form.add_error(field, msg)


@login_required
def home(request):
    acc = _account(request)
    plans = SavingsPlan.objects.filter(account=acc, status="active")
    closed = SavingsPlan.objects.filter(account=acc, status="closed")[:5]
    saved = plans.aggregate(t=Sum("balance"))["t"] or Decimal("0.00")
    return render(request, "savings/home.html", {
        "account": acc, "plans": plans, "closed": closed, "saved": saved,
        "flex_rate": rules.FLEX_RATE, "terms": rules.LOCKED_TERMS})


@login_required
def new_plan(request):
    acc = _account(request)
    ref = _ref(request)
    form = CreatePlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            plan = create_plan(
                acc, name=d["name"], kind=d["kind"], amount=d.get("amount") or 0, reference=ref,
                pin=d["pin"], term_days=int(d["term_days"]) if d.get("term_days") else None,
                target=d.get("target_amount"))
        except SavingsError as e:
            _fail(form, e)
        else:
            request.session.pop("sv_ref", None)
            messages.success(request, "Your savings plan is ready.")
            return redirect("savings:detail", pk=plan.pk)
    return _page(request, "New savings plan", form, "Create plan")


@login_required
def detail(request, pk):
    acc = _account(request)
    plan = _plan(acc, pk)
    pct = projected = None
    if plan.target_amount:
        pct = int(min(plan.balance / plan.target_amount * 100, 100))
    if plan.kind == "locked" and plan.status == "active":
        projected = (plan.principal * (1 + plan.rate / Decimal(36500)) ** plan.term_days
                     ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return render(request, "savings/detail.html", {
        "plan": plan, "entries": plan.entries.all()[:30], "pct": pct, "projected": projected,
        "matured": bool(plan.maturity_date and plan.maturity_date <= timezone.localdate())})


def _move(request, pk, title, button, action):
    acc = _account(request)
    plan = _plan(acc, pk)
    ref = _ref(request)
    form = MoveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            action(acc, plan.pk, form.cleaned_data["amount"], ref, form.cleaned_data["pin"])
        except SavingsError as e:
            _fail(form, e)
        else:
            request.session.pop("sv_ref", None)
            messages.success(request, "Done.")
            return redirect("savings:detail", pk=plan.pk)
    return _page(request, f"{title}: {plan.name}", form, button)


@login_required
def add_money(request, pk):
    return _move(request, pk, "Add money", "Add to savings", deposit)


@login_required
def take_out(request, pk):
    return _move(request, pk, "Withdraw", "Withdraw", withdraw)


@login_required
def close_plan(request, pk):
    acc = _account(request)
    plan = _plan(acc, pk)
    if plan.status == "closed":
        return redirect("savings:detail", pk=plan.pk)
    ref = _ref(request)
    form = PinForm(request.POST or None)
    warn = ""
    if plan.kind == "locked" and not (plan.maturity_date and plan.maturity_date <= timezone.localdate()):
        warn = (f"Breaking this plan early gives back your ₦{plan.principal:,.2f} "
                f"and you give up all the interest it has earned.")
    if request.method == "POST" and form.is_valid():
        act = break_locked if plan.kind == "locked" else close_flex
        try:
            act(acc, plan.pk, ref, form.cleaned_data["pin"])
        except SavingsError as e:
            _fail(form, e)
        else:
            request.session.pop("sv_ref", None)
            messages.success(request, "Your money is back in your account.")
            return redirect("savings:home")
    title = "Break locked plan" if plan.kind == "locked" else "Close plan"
    return _page(request, f"{title}: {plan.name}", form, "Confirm", warn)