import uuid
from decimal import Decimal
from pathlib import Path
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator


class User(AbstractUser):
    class Role(models.TextChoices):
        PATIENT = 'patient', 'Patient'
        RECEPTION = 'reception', 'Receptionist'
        DENTIST = 'dentist', 'Dentist'
        ADMIN = 'admin', 'Administrator'
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.PATIENT)
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_email_insensitive'),
                       models.UniqueConstraint(Lower('username'), name='unique_username_insensitive')]

    @property
    def clinic_staff(self):
        return self.is_superuser or self.role in [self.Role.RECEPTION, self.Role.DENTIST, self.Role.ADMIN]

    @property
    def clinical_access(self):
        return self.is_superuser or self.role in [self.Role.DENTIST, self.Role.ADMIN]


def patient_id():
    return 'DT-' + uuid.uuid4().hex[:12].upper()


class Patient(models.Model):
    pid = models.CharField(max_length=15, unique=True, default=patient_id, editable=False)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.PROTECT, related_name='patient')
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    birth_date = models.DateField()
    phone = models.CharField(max_length=30)
    address = models.TextField(blank=True)
    medical_history = models.TextField(blank=True)
    allergies = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name}'


class ActivationCode(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='activation_codes')
    code_hash = models.CharField(max_length=256)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Verification(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class AccessAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField()


class Service(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Appointment(models.Model):
    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        CONFIRMED = 'confirmed', 'Confirmed'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='appointments')
    dentist = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='appointments')
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['starts_at']
        constraints = [models.UniqueConstraint(fields=['dentist', 'starts_at'],
            condition=models.Q(status='confirmed'), name='unique_confirmed_dentist_slot')]


class Visit(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='visits')
    dentist = models.ForeignKey(User, on_delete=models.PROTECT)
    date = models.DateField()
    title = models.CharField(max_length=150)
    findings = models.TextField()
    procedures = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    prescription = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    released = models.BooleanField(default=False)

    class Meta:
        ordering = ['-date', '-pk']

    def __str__(self):
        return f'{self.date} · {self.title}'


class ToothRecord(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='teeth')
    number = models.PositiveSmallIntegerField(validators=[MinValueValidator(11), MaxValueValidator(85)])
    condition = models.CharField(max_length=20, choices=[('healthy', 'Healthy'), ('decay', 'Decay'),
        ('filled', 'Filled'), ('missing', 'Missing'), ('crown', 'Crown'), ('treatment', 'Needs treatment')])
    notes = models.CharField(max_length=250, blank=True)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['patient', 'number'], name='unique_patient_tooth')]


class TreatmentPlan(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='plans')
    title = models.CharField(max_length=150)
    details = models.TextField()
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=12, choices=[('proposed', 'Proposed'), ('ongoing', 'In progress'),
        ('completed', 'Completed')], default='proposed')
    released = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


def private_upload(instance, filename):
    return f'documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class Document(models.Model):
    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='documents')
    title = models.CharField(max_length=150)
    file = models.FileField(upload_to=private_upload,
        validators=[FileExtensionValidator(['pdf', 'png', 'jpg', 'jpeg'])])
    released = models.BooleanField(default=False)


class Invoice(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='invoices')
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    issued_at = models.DateField()

    class Meta:
        ordering = ['-issued_at', '-pk']

    @property
    def paid(self):
        return sum((p.amount for p in self.payments.all()), Decimal('0.00'))

    @property
    def balance(self):
        return self.amount - self.paid


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField(max_length=12, choices=[('cash', 'Cash'), ('card', 'Card'), ('transfer', 'Bank / e-wallet')])
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ClinicSettings(models.Model):
    name = models.CharField(max_length=120, default='DenTech Dental Clinic')
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    opening_hours = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
