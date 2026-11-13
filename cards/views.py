
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Account
from .forms import CardOrderForm
from .models import CardOrder

from .services import (
    CardIssuanceError,
    issue_virtual_card_order,
)


@login_required
def add_card(request):
    account = get_object_or_404(Account, user=request.user)

    if request.method == "POST":
        form = CardOrderForm(request.POST)

        if form.is_valid():
            order = form.save(commit=False)
            order.account = account

            if order.card_format == "physical":
                order.status = "pending"
                order.save()

                messages.success(
                    request,
                    "Your physical card request has been submitted "
                    "for staff review."
                )

            elif order.card_format == "virtual":
                order.status = "pending"
                order.save()

                try:
                    issue_virtual_card_order(order.pk)
                    messages.success(
            request,
            "Your virtual card has been issued."
        )
                except CardIssuanceError:
                    messages.error(
            request,
            "We could not issue your card. Please try again later."
        )

            return redirect(
                "cards:order_detail",
                reference=order.reference,
            )

    else:
        form = CardOrderForm()

    return render(request, "cards/add_card.html", {
        "form": form,
    })


@login_required
def card_orders(request):
    account = get_object_or_404(Account, user=request.user)

    orders = CardOrder.objects.filter(
        account=account
    ).select_related("bank_card")

    return render(request, "cards/orders.html", {
        "orders": orders,
    })


@login_required
def card_order_detail(request, reference):
    account = get_object_or_404(Account, user=request.user)

    order = get_object_or_404(
        CardOrder.objects.select_related("bank_card"),
        reference=reference,
        account=account,
    )

    return render(request, "cards/order_detail.html", {
        "order": order,
    })
