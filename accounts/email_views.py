from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from . import email_verify as ev
from .forms import CodeForm
from .utils import throttled


class EmailForm(forms.Form):
    email = forms.EmailField(max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))


def _page(request, title, form, button, status=200, error=""):
    return render(request, "accounts/form_page.html",
                  {"title": title, "form": form, "button": button, "error": error}, status=status)


@login_required
def start(request):
    if request.user.email_verified_at:
        messages.info(request, "Your email is already verified.")
        return redirect("accounts:upgrade")
    form = EmailForm(request.POST or None, initial={"email": request.user.email})
    if request.method == "POST":
        if throttled(request, "email_send", 10):
            return _page(request, "Verify your email", form, "Send code", 429, "Too many attempts. Try again later.")
        if form.is_valid():
            try:
                ev.send_code(request.user, form.cleaned_data["email"])
            except ValidationError as e:
                form.add_error("email", e.messages[0])
            else:
                messages.info(request, "We sent a 6-digit code to your email.")
                return redirect("accounts:email_verify")
    return _page(request, "Verify your email", form, "Send code")


@login_required
def verify(request):
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        if throttled(request, "email_check", 15):
            return _page(request, "Enter your code", form, "Verify", 429, "Too many attempts. Try again later.")
        if form.is_valid():
            if ev.check_code(request.user, form.cleaned_data["code"]):
                messages.success(request, "Your email is verified.")
                return redirect("accounts:upgrade")
            form.add_error("code", "That code is wrong or expired.")
    return _page(request, "Enter the code we emailed you", form, "Verify")
