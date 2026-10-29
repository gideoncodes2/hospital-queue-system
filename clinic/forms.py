from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from .models import Patient
from .utils import normalize_phone


class RegisterForm(forms.Form):
    full_name = forms.CharField(max_length=120, label="Full name")
    phone = forms.CharField(max_length=20, label="Phone number", help_text="Appointment texts are sent to this number.")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_phone(self):
        phone = normalize_phone_field(self.cleaned_data["phone"])
        if User.objects.filter(username=phone).exists():
            raise forms.ValidationError("An account with this phone number already exists. Log in instead.")
        return phone

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The passwords do not match.")
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        user = User.objects.create_user(username=d["phone"], password=d["password1"])
        # A walk-in may already have a record for this phone number: attach the account to it.
        patient, _ = Patient.objects.get_or_create(phone=d["phone"], defaults={"full_name": d["full_name"]})
        patient.user, patient.full_name = user, d["full_name"]
        patient.save()
        return user


class PhoneAuthenticationForm(AuthenticationForm):
    """Patients log in with a phone number in any common format. Staff use their username."""
    username = forms.CharField(label="Phone number or username")

    def clean(self):
        raw = self.cleaned_data.get("username")
        if raw:
            try:
                self.cleaned_data["username"] = normalize_phone(raw)
            except ValueError:
                pass
        return super().clean()


class WalkInForm(forms.Form):
    full_name = forms.CharField(max_length=120, label="Patient name")
    phone = forms.CharField(max_length=20, label="Phone number")

    def clean_phone(self):
        return normalize_phone_field(self.cleaned_data["phone"])


def normalize_phone_field(value):
    try:
        return normalize_phone(value)
    except ValueError as exc:
        raise forms.ValidationError(str(exc))
