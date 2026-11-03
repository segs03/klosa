from django import forms
from django.contrib.auth.models import User


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