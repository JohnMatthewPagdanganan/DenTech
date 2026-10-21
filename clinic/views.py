import calendar
import hashlib
import secrets
import time
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import *
from .models import ActivationCode, AuditEvent, Verification
from .services import audit, issue_activation, prepare_verification, send_verification, throttled, validate_activation


def staff_required(clinical=False, admin=False):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.clinic_staff or (clinical and not user.clinical_access) or (admin and not (user.is_superuser or user.role == 'admin')):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def own_patient(user):
    return get_object_or_404(Patient, user=user)


def home(request):
    return render(request, 'home.html', {'services': Service.objects.filter(active=True)[:6]})


def public_page(request, page):
    if page not in ['services', 'about', 'contact']:
        raise Http404
    return render(request, 'public.html', {'page': page, 'services': Service.objects.filter(active=True)})


def sign_in(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST':
        if throttled(request, 'login-ip', limit=40) or throttled(request, 'login', request.POST.get('username', ''), limit=8):
            form.add_error(None, 'Too many attempts. Please try again in 15 minutes.')
        elif form.is_valid():
            user = authenticate(request, username=form.cleaned_data['username'], password=form.cleaned_data['password'])
            if user:
                login(request, user)
                audit(user, 'Signed in', user.pk)
                return redirect('dashboard')
            form.add_error(None, 'Unable to sign in. Check your credentials and verify your email if you recently activated.')
    return render(request, 'auth/login.html', {'form': form})


@require_POST
def sign_out(request):
    logout(request)
    return redirect('home')


def activate(request):
    form = ActivationForm(request.POST or None)
    if request.method == 'POST':
        if throttled(request, 'activate', limit=15):
            form.add_error(None, 'Too many attempts. Please try again in 15 minutes.')
        elif form.is_valid():
            token = validate_activation(form.cleaned_data['patient_id'], form.cleaned_data['code'])
            if token:
                request.session.cycle_key()
                request.session['activation'] = {'id': token.pk, 'expires': time.time() + 600}
                return redirect('account_setup')
            form.add_error(None, 'The Patient ID or code is invalid, expired, or already used. Ask the clinic for a replacement.')
    return render(request, 'auth/activate.html', {'form': form})


def account_setup(request):
    grant = request.session.get('activation', {})
    if grant.get('expires', 0) < time.time():
        return redirect('activate')
    token = get_object_or_404(ActivationCode, pk=grant.get('id'))
    if token.used_at or token.expires_at <= timezone.now() or token.patient.user_id or token.attempts > 5:
        request.session.pop('activation', None)
        return redirect('activate')
    form = AccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                patient = Patient.objects.select_for_update().get(pk=token.patient_id)
                locked = ActivationCode.objects.select_for_update().get(pk=token.pk)
                if patient.user_id or locked.used_at or locked.expires_at <= timezone.now():
                    raise ValueError('This activation has expired or was already completed.')
                data = form.cleaned_data
                user = User.objects.create_user(username=data['username'], email=data['email'], password=data['password1'],
                    first_name=patient.first_name, last_name=patient.last_name, is_active=False)
                patient.user = user
                patient.save(update_fields=['user'])
                locked.used_at = timezone.now()
                locked.save(update_fields=['used_at'])
                email_token = prepare_verification(user)
                audit(user, 'Created portal account', patient.pid)
        except (IntegrityError, ValueError):
            form.add_error(None, 'Activation could not be completed. The account or activation details have changed.')
        else:
            request.session.pop('activation', None)
            try:
                send_verification(user, email_token)
                messages.success(request, 'Account created. Check your email to verify and activate it.')
            except Exception:
                messages.warning(request, 'Account created, but email could not be sent. Use Resend verification or contact the clinic.')
            return redirect('verification_pending')
    return render(request, 'auth/setup.html', {'form': form})


def verification_pending(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        if not throttled(request, 'resend', limit=5):
            user = User.objects.filter(email__iexact=email, role='patient', is_active=False, email_verified=False).first()
            if user and Patient.objects.filter(user=user).exists():
                token = prepare_verification(user)
                try:
                    send_verification(user, token)
                except Exception:
                    pass
        messages.info(request, 'If an eligible account exists, a new verification email has been requested. If it does not arrive, contact the clinic.')
    return render(request, 'auth/pending.html')


def verify_email(request, user_id, token):
    record = Verification.objects.filter(user_id=user_id).first()
    valid = record and not record.used_at and record.expires_at > timezone.now() and secrets.compare_digest(
        record.token_hash, hashlib.sha256(token.encode()).hexdigest())
    if request.method == 'POST' and valid:
        with transaction.atomic():
            record = Verification.objects.select_for_update().get(pk=record.pk)
            if record.used_at or record.expires_at <= timezone.now() or not secrets.compare_digest(record.token_hash, hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionDenied
            record.used_at = timezone.now()
            record.save(update_fields=['used_at'])
            User.objects.filter(pk=user_id, email_verified=False).update(email_verified=True, is_active=True)
            audit(record.user, 'Verified email', user_id)
        messages.success(request, 'Email verified. You can now sign in with your username and password.')
        return redirect('login')
    return render(request, 'auth/verify.html', {'valid': valid})


@login_required
def dashboard(request):
    staff = request.user.clinic_staff
    patient = None if staff else own_patient(request.user)
    appointments = Appointment.objects.select_related('patient', 'service', 'dentist')
    invoices = Invoice.objects.prefetch_related('payments')
    if not staff:
        appointments = appointments.filter(patient=patient)
        invoices = invoices.filter(patient=patient)
    upcoming = appointments.filter(starts_at__gte=timezone.now()).exclude(status__in=['cancelled', 'completed'])
    return render(request, 'dashboard.html', {'patient': patient, 'upcoming': upcoming[:5],
        'appointment_count': appointments.filter(starts_at__date=timezone.localdate()).count() if staff else upcoming.count(),
        'patient_count': Patient.objects.count() if staff else patient.visits.filter(released=True).count(),
        'balance': sum((i.balance for i in invoices), Decimal('0.00')),
        'pending_count': appointments.filter(status='requested').count(),
        'recent_visits': Visit.objects.filter(released=True, patient=patient)[:3] if patient else []})


@staff_required()
def patients(request):
    query = request.GET.get('q', '').strip()
    rows = Patient.objects.select_related('user').all()
    if query:
        rows = rows.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(pid__icontains=query) | Q(phone__icontains=query))
    from django.core.paginator import Paginator
    return render(request, 'patients.html', {'patients': Paginator(rows, 25).get_page(request.GET.get('page')), 'query': query})


@staff_required()
def patient_create(request):
    form = PatientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        if Patient.objects.filter(first_name__iexact=data['first_name'], last_name__iexact=data['last_name'], birth_date=data['birth_date']).exists():
            form.add_error(None, 'A patient with this name and birth date already exists. Search the patient list before registering again.')
        else:
            patient = form.save()
            audit(request.user, 'Registered patient', patient.pid)
            messages.success(request, 'Patient registered. Issue an activation code to enable portal access.')
            return redirect('patient_detail', pk=patient.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Register a patient', 'subtitle': 'Create one clinic record for all future visits.'})


@staff_required()
def patient_detail(request, pk):
    patient = get_object_or_404(Patient.objects.select_related('user'), pk=pk)
    audit(request.user, 'Viewed patient record', patient.pid)
    teeth = {t.number: t for t in patient.teeth.all()} if request.user.clinical_access else {}
    rows = [[teeth.get(n, {'number': n, 'condition': 'unrecorded', 'get_condition_display': 'Not recorded'}) for n in row]
        for row in [list(range(18, 10, -1)) + list(range(21, 29)), list(range(48, 40, -1)) + list(range(31, 39)),
                    list(range(55, 50, -1)) + list(range(61, 66)), list(range(85, 80, -1)) + list(range(71, 76))]]
    return render(request, 'patient_detail.html', {'patient': patient, 'tooth_rows': rows,
        'visits': patient.visits.all() if request.user.clinical_access else [],
        'plans': patient.plans.all() if request.user.clinical_access else [],
        'invoices': patient.invoices.prefetch_related('payments')})


@staff_required()
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request.user, 'Updated patient details', patient.pid)
        return redirect('patient_detail', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': 'Edit patient details'})


@staff_required()
@require_POST
def activation_issue(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    try:
        code = issue_activation(patient, request.user)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect('patient_detail', pk=pk)
    return render(request, 'activation_receipt.html', {'patient': patient, 'code': code, 'portal_url': settings.PUBLIC_BASE_URL + '/activate/'})


@login_required
def appointments(request):
    rows = Appointment.objects.select_related('patient', 'service', 'dentist')
    if not request.user.clinic_staff:
        rows = rows.filter(patient=own_patient(request.user))
    if request.GET.get('status') in Appointment.Status.values:
        rows = rows.filter(status=request.GET['status'])
    return render(request, 'appointments.html', {'appointments': rows.order_by('-starts_at')[:100], 'statuses': Appointment.Status.choices})


@login_required
def appointment_calendar(request):
    today = timezone.localdate()
    try:
        month = date.fromisoformat(request.GET.get('month', today.strftime('%Y-%m-01'))).replace(day=1)
        if not 1901 <= month.year <= 2098:
            raise ValueError
    except ValueError:
        month = today.replace(day=1)
    previous = (month - timedelta(days=1)).replace(day=1)
    following = (month + timedelta(days=32)).replace(day=1)
    rows = Appointment.objects.select_related('patient', 'service').filter(starts_at__date__gte=month,
        starts_at__date__lt=following).exclude(status='cancelled')
    if not request.user.clinic_staff:
        rows = rows.filter(patient=own_patient(request.user))
    by_date = {}
    for appointment in rows:
        by_date.setdefault(timezone.localtime(appointment.starts_at).date(), []).append(appointment)
    weeks = [[{'day': day, 'current': day.month == month.month, 'today': day == today,
               'appointments': by_date.get(day, [])} for day in week]
             for week in calendar.Calendar().monthdatescalendar(month.year, month.month)]
    return render(request, 'calendar.html', {'month': month, 'previous': previous, 'following': following, 'weeks': weeks})


@login_required
def appointment_create(request):
    staff = request.user.clinic_staff
    form = AppointmentForm(request.POST or None, staff=staff)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.patient = form.cleaned_data['patient'] if staff else own_patient(request.user)
        item.save()
        audit(request.user, 'Requested appointment', item.pk)
        messages.success(request, 'Appointment requested. The clinic will confirm your schedule.')
        return redirect('appointments')
    return render(request, 'form.html', {'form': form, 'title': 'Request an appointment', 'subtitle': 'Your preferred time is subject to clinic confirmation.'})


@staff_required()
def appointment_update(request, pk):
    item = get_object_or_404(Appointment, pk=pk)
    form = AppointmentStatusForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, 'That dentist already has a confirmed appointment at this time.')
        else:
            audit(request.user, 'Updated appointment', item.pk)
            return redirect('appointments')
    return render(request, 'form.html', {'form': form, 'title': 'Manage appointment', 'subtitle': str(item.patient) + ' · ' + str(item.service)})


@login_required
@require_POST
def appointment_cancel(request, pk):
    rows = Appointment.objects.all()
    if not request.user.clinic_staff:
        rows = rows.filter(patient=own_patient(request.user))
    item = get_object_or_404(rows, pk=pk)
    if item.status in ['requested', 'confirmed'] and item.starts_at > timezone.now():
        item.status = 'cancelled'
        item.save(update_fields=['status'])
        audit(request.user, 'Cancelled appointment', item.pk)
    return redirect('appointments')


@staff_required(clinical=True)
def clinical_form(request, pk, kind, item_id=None):
    patient = get_object_or_404(Patient, pk=pk)
    if kind not in ['visit', 'plan', 'history', 'tooth']:
        raise Http404
    form_class, model, title = {'visit': (VisitForm, Visit, 'Visit record'), 'plan': (PlanForm, TreatmentPlan, 'Treatment plan'),
        'history': (HistoryForm, Patient, 'Medical history'), 'tooth': (ToothForm, ToothRecord, 'Update dental chart')}[kind]
    instance = patient if kind == 'history' else get_object_or_404(model, pk=item_id, patient=patient) if item_id else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        if kind != 'history':
            item.patient = patient
        if kind == 'visit' and not item.pk:
            item.dentist = request.user
        if kind == 'tooth':
            ToothRecord.objects.update_or_create(patient=patient, number=form.cleaned_data['number'], defaults={
                'condition': form.cleaned_data['condition'], 'notes': form.cleaned_data['notes'], 'updated_by': request.user})
        else:
            item.save()
        audit(request.user, 'Updated ' + kind, patient.pid)
        return redirect('patient_detail', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': title, 'subtitle': str(patient)})


@staff_required(clinical=True)
def document_upload(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.visit = visit
        item.save()
        audit(request.user, 'Uploaded document', item.pk)
        return redirect('visit_detail', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': 'Attach document', 'subtitle': 'PDF, PNG, or JPEG · Maximum 10 MB'})


@login_required
def visits(request):
    if request.user.clinical_access:
        rows = Visit.objects.select_related('patient', 'dentist').all()
    elif request.user.clinic_staff:
        raise PermissionDenied
    else:
        rows = own_patient(request.user).visits.filter(released=True)
    return render(request, 'visits.html', {'visits': rows[:100]})


@login_required
def visit_detail(request, pk):
    rows = Visit.objects.select_related('patient', 'dentist')
    if not request.user.clinical_access:
        if request.user.clinic_staff:
            raise PermissionDenied
        rows = rows.filter(patient=own_patient(request.user), released=True)
    visit = get_object_or_404(rows, pk=pk)
    documents = visit.documents.all() if request.user.clinical_access else visit.documents.filter(released=True)
    audit(request.user, 'Viewed visit', visit.pk)
    return render(request, 'visit_detail.html', {'visit': visit, 'documents': documents})


@login_required
def documents(request):
    rows = Document.objects.select_related('visit', 'visit__patient')
    if not request.user.clinical_access:
        if request.user.clinic_staff:
            raise PermissionDenied
        rows = rows.filter(visit__patient=own_patient(request.user), visit__released=True, released=True)
    return render(request, 'documents.html', {'documents': rows.order_by('-pk')[:100]})


@login_required
def document_download(request, pk):
    rows = Document.objects.select_related('visit')
    if not request.user.clinical_access:
        if request.user.clinic_staff:
            raise PermissionDenied
        rows = rows.filter(visit__patient=own_patient(request.user), visit__released=True, released=True)
    item = get_object_or_404(rows, pk=pk)
    audit(request.user, 'Downloaded document', item.pk)
    try:
        response = FileResponse(item.file.open('rb'), as_attachment=True, filename='document' + Path(item.file.name).suffix)
    except FileNotFoundError:
        raise Http404
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
def plans(request):
    if request.user.clinical_access:
        rows = TreatmentPlan.objects.select_related('patient').all()
    elif request.user.clinic_staff:
        raise PermissionDenied
    else:
        rows = own_patient(request.user).plans.filter(released=True)
    return render(request, 'plans.html', {'plans': rows.order_by('-pk')[:100]})


@login_required
def billing(request):
    rows = Invoice.objects.select_related('patient').prefetch_related('payments')
    if not request.user.clinic_staff:
        rows = rows.filter(patient=own_patient(request.user))
    return render(request, 'billing.html', {'invoices': rows[:100]})


@staff_required()
def invoice_create(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = InvoiceForm(request.POST or None, initial={'issued_at': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        invoice = form.save(commit=False)
        invoice.patient = patient
        invoice.save()
        audit(request.user, 'Created invoice', invoice.pk)
        return redirect('receipt', pk=invoice.pk)
    return render(request, 'form.html', {'form': form, 'title': 'Create invoice', 'subtitle': str(patient)})


@staff_required()
def payment_create(request, pk):
    invoice = get_object_or_404(Invoice.objects.prefetch_related('payments'), pk=pk)
    form = PaymentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = Invoice.objects.select_for_update().get(pk=pk)
            if form.cleaned_data['amount'] > locked.balance:
                form.add_error('amount', 'Payment exceeds the outstanding balance.')
            else:
                payment = form.save(commit=False)
                payment.invoice = locked
                payment.received_by = request.user
                payment.save()
                audit(request.user, 'Recorded payment', payment.pk)
                return redirect('receipt', pk=pk)
    return render(request, 'form.html', {'form': form, 'title': 'Record payment', 'subtitle': f'Invoice #{pk:05d} · Outstanding ₱{invoice.balance:,.2f}'})


@login_required
def receipt(request, pk):
    rows = Invoice.objects.select_related('patient').prefetch_related('payments')
    if not request.user.clinic_staff:
        rows = rows.filter(patient=own_patient(request.user))
    invoice = get_object_or_404(rows, pk=pk)
    audit(request.user, 'Viewed invoice', invoice.pk)
    return render(request, 'receipt.html', {'invoice': invoice, 'portal_url': settings.PUBLIC_BASE_URL + '/activate/'})


@login_required
def profile(request):
    if request.user.clinic_staff:
        return render(request, 'profile.html')
    patient = own_patient(request.user)
    form = ProfileForm(request.POST or None, instance=patient)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request.user, 'Updated contact details', patient.pid)
        messages.success(request, 'Your contact information has been updated.')
        return redirect('profile')
    return render(request, 'profile.html', {'form': form, 'patient': patient})


@staff_required(admin=True)
def reports(request):
    return render(request, 'reports.html', {'patient_count': Patient.objects.count(),
        'appointment_count': Appointment.objects.count(), 'collected': Payment.objects.aggregate(total=Sum('amount'))['total'] or 0,
        'balance': sum((i.balance for i in Invoice.objects.prefetch_related('payments')), Decimal('0.00')),
        'events': AuditEvent.objects.select_related('actor')[:50]})


@staff_required(admin=True)
def clinic_settings(request):
    instance, _ = ClinicSettings.objects.get_or_create(pk=1)
    form = ClinicSettingsForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request.user, 'Updated clinic settings', 1)
        messages.success(request, 'Clinic details updated.')
        return redirect('clinic_settings')
    return render(request, 'form.html', {'form': form, 'title': 'Clinic settings',
        'subtitle': 'These details appear on the public contact page and billing summaries.'})
