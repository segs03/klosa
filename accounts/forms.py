from django import forms
from django.contrib.auth import get_user_model

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from .utils import normalize_email, normalize_phone


User = get_user_model()


WEAK_PINS = {"0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777",
             "8888", "9999", "1234", "4321", "1212", "2580", "0852"}


class SetPinForm(forms.Form):
    current_password = forms.CharField(required=False, widget=forms.PasswordInput)
    pin = forms.CharField(min_length=4, max_length=4, widget=forms.PasswordInput(
        attrs={"inputmode": "numeric", "autocomplete": "off"}))
    confirm_pin = forms.CharField(min_length=4, max_length=4, widget=forms.PasswordInput(
        attrs={"inputmode": "numeric", "autocomplete": "off"}))

    def __init__(self, *args, user=None, has_pin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user, self.has_pin = user, has_pin
        if not has_pin:
            del self.fields["current_password"]      # first PIN: no password needed

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not pin.isdigit():
            raise forms.ValidationError("PIN must be 4 digits.")
        if pin in WEAK_PINS or pin in "0123456789" or pin in "9876543210":
            raise forms.ValidationError("That PIN is too easy to guess.")
        return pin

    def clean(self):
        data = super().clean()
        if data.get("pin") and data["pin"] != data.get("confirm_pin"):
            self.add_error("confirm_pin", "PINs do not match.")
        if self.has_pin and not self.user.check_password(data.get("current_password", "")):
            self.add_error("current_password", "Incorrect password.")
        return data



class PhoneSignupForm(forms.Form):
    phone = forms.CharField(max_length=20, widget=forms.TextInput(
        attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "080 1234 5678"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    agree = forms.BooleanField(label="I agree to the Terms and Privacy Policy")

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not phone:
            raise forms.ValidationError("Enter a valid Nigerian mobile number.")
        return phone

    def clean_password(self):
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw


class PhoneForm(forms.Form):
    phone = forms.CharField(max_length=20, widget=forms.TextInput(
        attrs={"inputmode": "tel", "autocomplete": "tel"}))

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not phone:
            raise forms.ValidationError("Enter a valid Nigerian mobile number.")
        return phone



class CodeForm(forms.Form):
    code = forms.CharField(min_length=6, max_length=6, widget=forms.TextInput(
        attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


class RecoverForm(forms.Form):
    code = forms.CharField(min_length=6, max_length=6, widget=forms.TextInput(
        attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))
    backup_code = forms.CharField(required=False, label="Backup code (optional)")
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_new_password(self):
        pw = self.cleaned_data["new_password"]
        validate_password(pw)
        return pw


class StaffCreateForm(forms.ModelForm):
    role = forms.ChoiceField(choices=[("Support", "Support"), ("Compliance", "Compliance"),
                                      ("Operations", "Operations")])

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("Staff need an email address.")
        if User.objects.filter(email_normalized=normalize_email(email)).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        if not Group.objects.filter(name=role).exists():
            raise forms.ValidationError("Roles are not set up. Run: python manage.py setup_roles")
        return role



class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

        widgets = {
            "first_name": forms.TextInput(attrs={
                "placeholder": "First name"
            }),
            "last_name": forms.TextInput(attrs={
                "placeholder": "Last name"
            }),
            "email": forms.EmailInput(attrs={
                "placeholder": "Email address"
            }),
        }
        
        
class CloseAccountForm(forms.Form):
    password = forms.CharField(label="Your password", widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password"}))
    pin = forms.CharField(label="Transaction PIN", min_length=4, max_length=4, widget=forms.PasswordInput(
        attrs={"inputmode": "numeric", "autocomplete": "off"}))
    confirm = forms.CharField(label="Type CLOSE to confirm", widget=forms.TextInput(
        attrs={"autocapitalize": "characters", "autocomplete": "off"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if not self.user.check_password(pw):
            raise forms.ValidationError("Incorrect password.")
        return pw

    def clean_confirm(self):
        if self.cleaned_data["confirm"].strip().upper() != "CLOSE":
            raise forms.ValidationError("Type the word CLOSE to continue.")
        return "CLOSE"