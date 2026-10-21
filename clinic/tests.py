import hashlib
import re
from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import *
from .services import issue_activation, validate_activation, prepare_verification


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ClinicTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user('reception', 'desk@example.test', 'testpass-long', role='reception')
        self.dentist = User.objects.create_user('dentist', 'dentist@example.test', 'testpass-long', role='dentist')
        self.admin = User.objects.create_superuser('admin', 'admin@example.test', 'testpass-long', role='admin')
        self.user = User.objects.create_user('patient', 'patient@example.test', 'testpass-long', email_verified=True)
        self.other_user = User.objects.create_user('other', 'other@example.test', 'testpass-long', email_verified=True)
        data = {'first_name': 'Sam', 'last_name': 'Cruz', 'birth_date': '1998-04-12', 'phone': '09000000000'}
        self.patient = Patient.objects.create(user=self.user, **data)
        self.other = Patient.objects.create(user=self.other_user, **{**data, 'first_name': 'Other'})
        self.new = Patient.objects.create(**{**data, 'first_name': 'New'})
        self.service = Service.objects.create(name='Consultation', description='A dental assessment.', price=500)
        self.visit = Visit.objects.create(patient=self.patient, dentist=self.dentist, date=timezone.localdate(), title='Checkup', findings='Released findings', internal_notes='PRIVATE STAFF NOTE', released=True)
        self.draft = Visit.objects.create(patient=self.patient, dentist=self.dentist, date=timezone.localdate(), title='Draft', findings='UNRELEASED FINDINGS')
        self.foreign_visit = Visit.objects.create(patient=self.other, dentist=self.dentist, date=timezone.localdate(), title='Other visit', findings='OTHER PATIENT', released=True)
        self.invoice = Invoice.objects.create(patient=self.patient, description='Consultation', amount=500, issued_at=timezone.localdate())

    def test_public_pages(self):
        for url in ['/', '/info/about/', '/info/services/', '/info/contact/', '/login/', '/activate/', '/reset/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_calendar_is_scoped_to_patient(self):
        starts = timezone.now() + timedelta(days=2)
        Appointment.objects.create(patient=self.other, service=self.service, starts_at=starts, notes='Other patient')
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/appointments/calendar/?month=' + timezone.localtime(starts).strftime('%Y-%m-01')), 'Consultation')
        self.assertEqual(self.client.get('/appointments/calendar/?month=bad').status_code, 200)

    def test_clinic_settings_permissions(self):
        self.client.force_login(self.reception)
        self.assertEqual(self.client.get('/settings/').status_code, 403)
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.post('/settings/', {'name': 'Test Clinic', 'phone': '555-0100'}), '/settings/')
        self.client.logout()
        self.assertContains(self.client.get('/info/contact/'), '555-0100')

    def test_full_activation_and_email_verification(self):
        code = issue_activation(self.new, self.reception)
        self.assertNotEqual(self.new.activation_codes.latest('pk').code_hash, code)
        response = self.client.post('/activate/', {'patient_id': self.new.pid, 'code': code})
        self.assertRedirects(response, '/activate/setup/')
        response = self.client.post('/activate/setup/', {'email': 'New@Example.test', 'username': 'newpatient',
            'password1': 'Willow-River-93!', 'password2': 'Willow-River-93!'})
        self.assertRedirects(response, '/verify/pending/')
        self.new.refresh_from_db()
        user = self.new.user
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, 'new@example.test')
        self.assertTrue(user.check_password('Willow-River-93!'))
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'http://127.0.0.1:8000(/verify/[^\s]+)', mail.outbox[0].body).group(1)
        self.client.get(url)
        user.refresh_from_db()
        self.assertFalse(user.is_active, 'Email links must not mutate on GET')
        self.assertRedirects(self.client.post(url), '/login/')
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        self.assertContains(self.client.get(url), 'no longer valid')
        self.assertIsNone(validate_activation(self.new.pid, code))
        self.assertRedirects(self.client.post('/login/', {'username': 'newpatient', 'password': 'Willow-River-93!'}), '/dashboard/')
        self.assertContains(self.client.get('/dashboard/'), 'New')

    def test_replacement_code_revokes_old_code(self):
        old = issue_activation(self.new, self.reception)
        new = issue_activation(self.new, self.reception)
        self.assertIsNone(validate_activation(self.new.pid, old))
        self.assertIsNotNone(validate_activation(self.new.pid, new))

    def test_expired_code_is_rejected(self):
        code = issue_activation(self.new, self.reception)
        self.new.activation_codes.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(validate_activation(self.new.pid, code))

    def test_code_locks_after_five_wrong_attempts(self):
        code = issue_activation(self.new, self.reception)
        for _ in range(5):
            self.assertIsNone(validate_activation(self.new.pid, 'WRONG'))
        self.assertIsNone(validate_activation(self.new.pid, code))
        self.assertEqual(self.new.activation_codes.latest('pk').attempts, 5)

    def test_setup_requires_valid_session(self):
        self.assertRedirects(self.client.get('/activate/setup/'), '/activate/')

    def test_expired_setup_session(self):
        code = issue_activation(self.new, self.reception)
        self.client.post('/activate/', {'patient_id': self.new.pid, 'code': code})
        session = self.client.session
        session['activation'] = {'id': self.new.activation_codes.latest('pk').pk, 'expires': 0}
        session.save()
        self.assertRedirects(self.client.get('/activate/setup/'), '/activate/')

    def test_cannot_issue_code_for_linked_patient(self):
        with self.assertRaises(ValueError):
            issue_activation(self.patient, self.reception)

    def test_unverified_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post('/login/', {'username': 'patient', 'password': 'testpass-long'})
        self.assertContains(response, 'Unable to sign in')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_rate_limit(self):
        for _ in range(8):
            self.client.post('/login/', {'username': 'patient', 'password': 'wrong'})
        response = self.client.post('/login/', {'username': 'patient', 'password': 'testpass-long'})
        self.assertContains(response, 'Too many attempts')

    def test_verification_expiry_and_resend(self):
        self.user.is_active = False
        self.user.email_verified = False
        self.user.save()
        token = prepare_verification(self.user)
        Verification.objects.filter(user=self.user).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(self.client.get(f'/verify/{self.user.pk}/{token}/'), 'no longer valid')
        self.client.post('/verify/pending/', {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)

    def test_account_form_rejects_duplicate_email_ignoring_case(self):
        from .forms import AccountForm
        form = AccountForm({'email': self.user.email.upper(), 'username': 'fresh', 'password1': 'Willow-River-93!', 'password2': 'Willow-River-93!'})
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_patient_only_sees_released_own_visits(self):
        self.client.force_login(self.user)
        response = self.client.get('/visits/')
        self.assertContains(response, 'Checkup')
        self.assertNotContains(response, 'Other visit')
        self.assertNotContains(response, '>Draft<')
        detail = self.client.get(f'/visits/{self.visit.pk}/')
        self.assertContains(detail, 'Released findings')
        self.assertNotContains(detail, 'PRIVATE STAFF NOTE')
        self.assertEqual(self.client.get(f'/visits/{self.draft.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/visits/{self.foreign_visit.pk}/').status_code, 404)

    def test_reception_cannot_access_clinical_records(self):
        self.client.force_login(self.reception)
        for url in ['/visits/', '/documents/', '/plans/', f'/visits/{self.visit.pk}/', f'/patients/{self.patient.pk}/clinical/visit/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.get(f'/patients/{self.patient.pk}/')
        self.assertNotContains(response, 'Dental chart')
        self.assertNotContains(response, 'Released findings')

    def test_patient_cannot_access_staff_routes(self):
        self.client.force_login(self.user)
        for url in ['/patients/', '/patients/new/', '/reports/', f'/patients/{self.patient.pk}/', f'/billing/{self.invoice.pk}/payment/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_patient_cannot_access_another_invoice(self):
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(f'/billing/{self.invoice.pk}/').status_code, 404)
        self.assertNotContains(self.client.get('/billing/'), 'Consultation')

    def test_document_download_authorization(self):
        with TemporaryDirectory(dir=settings.BASE_DIR) as directory, override_settings(MEDIA_ROOT=directory):
            doc = Document.objects.create(visit=self.visit, title='X-ray', released=True,
                file=SimpleUploadedFile('scan.pdf', b'%PDF-1.4 demo'))
            self.client.force_login(self.user)
            response = self.client.get(f'/documents/{doc.pk}/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response['Content-Disposition'])
            response.close()
            doc.released = False
            doc.save()
            self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code, 404)
            doc.released = True
            doc.save()
            self.client.force_login(self.other_user)
            self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code, 404)
            self.client.force_login(self.reception)
            self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code, 403)

    def test_document_under_draft_visit_is_hidden(self):
        Document.objects.create(visit=self.draft, title='Draft X-ray', released=True, file='documents/draft.pdf')
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/documents/'), 'Draft X-ray')

    def test_invalid_upload_is_rejected(self):
        from .forms import DocumentForm
        form = DocumentForm({'title': 'Bad'}, {'file': SimpleUploadedFile('bad.pdf', b'<script>alert(1)</script>')})
        self.assertFalse(form.is_valid())

    def test_only_released_plans_visible(self):
        TreatmentPlan.objects.create(patient=self.patient, title='Visible plan', details='Details', estimated_cost=500, released=True)
        TreatmentPlan.objects.create(patient=self.patient, title='Private plan', details='Details', estimated_cost=500)
        self.client.force_login(self.user)
        response = self.client.get('/plans/')
        self.assertContains(response, 'Visible plan')
        self.assertNotContains(response, 'Private plan')

    def test_appointment_patient_cannot_spoof_owner(self):
        self.client.force_login(self.user)
        starts = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post('/appointments/new/', {'patient': self.other.pk, 'service': self.service.pk, 'starts_at': starts})
        self.assertRedirects(response, '/appointments/')
        self.assertEqual(Appointment.objects.get().patient, self.patient)
        self.assertEqual(Appointment.objects.get().status, 'requested')

    def test_past_appointment_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post('/appointments/new/', {'service': self.service.pk, 'starts_at': '2000-01-01T10:00'})
        self.assertContains(response, 'Choose a future')
        self.assertEqual(Appointment.objects.count(), 0)

    def test_appointment_collision(self):
        starts = timezone.now() + timedelta(days=2)
        Appointment.objects.create(patient=self.patient, service=self.service, dentist=self.dentist, starts_at=starts, status='confirmed')
        second = Appointment.objects.create(patient=self.other, service=self.service, starts_at=starts)
        self.client.force_login(self.reception)
        response = self.client.post(f'/appointments/{second.pk}/manage/', {'dentist': self.dentist.pk, 'status': 'confirmed', 'starts_at': timezone.localtime(starts).isoformat()})
        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.status, 'requested')

    def test_cannot_cancel_other_patient_appointment(self):
        item = Appointment.objects.create(patient=self.other, service=self.service, starts_at=timezone.now() + timedelta(days=2))
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f'/appointments/{item.pk}/cancel/').status_code, 404)

    def test_payment_cannot_exceed_balance(self):
        self.client.force_login(self.reception)
        self.client.post(f'/billing/{self.invoice.pk}/payment/', {'amount': '300', 'method': 'cash'})
        response = self.client.post(f'/billing/{self.invoice.pk}/payment/', {'amount': '250', 'method': 'cash'})
        self.assertContains(response, 'exceeds the outstanding')
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Invoice.objects.get(pk=self.invoice.pk).balance, Decimal('200'))

    def test_negative_payment_rejected(self):
        self.client.force_login(self.reception)
        response = self.client.post(f'/billing/{self.invoice.pk}/payment/', {'amount': '-10', 'method': 'cash'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    def test_duplicate_patient_rejected(self):
        self.client.force_login(self.reception)
        response = self.client.post('/patients/new/', {'first_name': 'sam', 'last_name': 'CRUZ', 'birth_date': '1998-04-12', 'phone': '09000000000'})
        self.assertContains(response, 'already exists')
        self.assertEqual(Patient.objects.count(), 3)

    def test_tooth_update_replaces_existing(self):
        self.client.force_login(self.dentist)
        url = f'/patients/{self.patient.pk}/clinical/tooth/'
        self.client.post(url, {'number': 11, 'condition': 'healthy'})
        self.client.post(url, {'number': 11, 'condition': 'decay'})
        self.assertEqual(ToothRecord.objects.count(), 1)
        self.assertEqual(ToothRecord.objects.get().condition, 'decay')

    def test_role_screens_render(self):
        routes = ['/dashboard/', '/appointments/', '/billing/', '/profile/']
        for user in [self.admin, self.dentist, self.reception, self.user]:
            self.client.force_login(user)
            for url in routes:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.admin)
        for url in ['/reports/', '/patients/', f'/patients/{self.patient.pk}/', '/visits/', '/documents/', '/plans/', f'/billing/{self.invoice.pk}/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_private_pages_disable_caching(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/dashboard/')['Cache-Control'], 'no-store, private')

    def test_csrf_and_post_only_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reception)
        self.assertEqual(client.post(f'/patients/{self.new.pk}/activation/').status_code, 403)
        self.assertEqual(self.client.get('/logout/').status_code, 405)

    def test_password_reset_email_uses_configured_host(self):
        response = self.client.post('/reset/', {'email': self.user.email}, HTTP_HOST='testserver')
        self.assertRedirects(response, '/reset/sent/')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('http://127.0.0.1:8000/reset/', mail.outbox[0].body)
        self.assertIn('Your username is patient', mail.outbox[0].body)

    def test_patient_contact_update_cannot_change_identity(self):
        self.client.force_login(self.user)
        self.client.post('/profile/', {'phone': '09111111111', 'address': 'Updated', 'first_name': 'Spoofed', 'role': 'admin'})
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.first_name, 'Sam')
        self.assertEqual(self.patient.phone, '09111111111')
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'patient')
