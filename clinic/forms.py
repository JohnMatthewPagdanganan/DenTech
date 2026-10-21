from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import User, Patient, Service, Appointment, Visit, ToothRecord, TreatmentPlan, Document, Invoice, Payment
from .models import ClinicSettings


class ClinicSettingsForm(forms.ModelForm):
    class Meta:
        model = ClinicSettings
        fields = ['name', 'address', 'phone', 'email', 'opening_hours']


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'autocomplete': 'username'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


class ActivationForm(forms.Form):
    patient_id = forms.CharField(max_length=15, label='Patient ID', help_text='Printed on your clinic receipt.')
    code = forms.CharField(max_length=20, label='One-time activation code', widget=forms.PasswordInput)

    def clean_patient_id(self):
        return self.cleaned_data['patient_id'].strip().upper()


class AccountForm(forms.Form):
    email = forms.EmailField()
    username = forms.CharField(max_length=150, validators=User._meta.get_field('username').validators)
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput,
        help_text='Use at least 10 characters. Avoid common passwords and personal information.')
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput)

    def clean_email(self):
        value = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise ValidationError('This email cannot be used. Sign in or contact the clinic.')
        return value

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=value).exists():
            raise ValidationError('This username is unavailable.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'Passwords do not match.')
        if data.get('password1'):
            password_validation.validate_password(data['password1'], User(username=data.get('username', ''), email=data.get('email', '')))
        return data


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['first_name', 'last_name', 'birth_date', 'phone', 'address']
        widgets = {'birth_date': forms.DateInput(attrs={'type': 'date'})}

    def clean_birth_date(self):
        date = self.cleaned_data['birth_date']
        if date > timezone.localdate():
            raise ValidationError('Birth date cannot be in the future.')
        return date


class HistoryForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['medical_history', 'allergies']


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['phone', 'address']


class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['service', 'starts_at', 'notes']
        widgets = {'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'})}

    def __init__(self, *args, staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['service'].queryset = Service.objects.filter(active=True)
        if staff:
            self.fields['patient'] = forms.ModelChoiceField(Patient.objects.all())

    def clean_starts_at(self):
        value = self.cleaned_data['starts_at']
        if value <= timezone.now():
            raise ValidationError('Choose a future appointment time.')
        return value


class AppointmentStatusForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['status', 'dentist', 'starts_at']
        widgets = {'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dentist'].queryset = User.objects.filter(role=User.Role.DENTIST, is_active=True)

    def clean(self):
        data = super().clean()
        if data.get('status') == 'confirmed' and not data.get('dentist'):
            self.add_error('dentist', 'Assign a dentist before confirming.')
        if data.get('status') == 'confirmed' and data.get('starts_at') and data['starts_at'] <= timezone.now():
            self.add_error('starts_at', 'Choose a future time to confirm.')
        return data


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = ['date', 'title', 'findings', 'procedures', 'instructions', 'prescription', 'internal_notes', 'released']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


class ToothForm(forms.ModelForm):
    class Meta:
        model = ToothRecord
        fields = ['number', 'condition', 'notes']

    def clean_number(self):
        number = self.cleaned_data['number']
        valid = [q * 10 + t for q in range(1, 5) for t in range(1, 9)] + [q * 10 + t for q in range(5, 9) for t in range(1, 6)]
        if number not in valid:
            raise ValidationError('Enter a valid FDI tooth number (11–48 or 51–85).')
        return number


class PlanForm(forms.ModelForm):
    class Meta:
        model = TreatmentPlan
        fields = ['title', 'details', 'estimated_cost', 'status', 'released']


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'file', 'released']

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > 10 * 1024 * 1024:
            raise ValidationError('Files must be 10 MB or smaller.')
        head = file.read(8)
        file.seek(0)
        if not (head.startswith(b'%PDF-') or head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff')):
            raise ValidationError('Upload a valid PDF, PNG, or JPEG file.')
        return file


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['description', 'amount', 'issued_at']
        widgets = {'issued_at': forms.DateInput(attrs={'type': 'date'})}


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'method', 'reference']
