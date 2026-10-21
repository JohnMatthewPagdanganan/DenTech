import secrets
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from clinic.models import User, Patient, Service, Appointment, Visit, TreatmentPlan, Invoice, Payment, ToothRecord


class Command(BaseCommand):
    help = 'Create fictional local demo records. Never run against a real clinic database.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo data is allowed only with DJANGO_DEBUG=1.')
        if User.objects.filter(username='demo.admin').exists():
            self.stdout.write('Demo data already exists. Existing accounts were not changed.')
            return
        password = secrets.token_urlsafe(14)
        admin = User.objects.create_superuser('demo.admin', 'admin@example.test', password, first_name='Alex', last_name='Rivera', role='admin', email_verified=True)
        dentist = User.objects.create_user('demo.dentist', 'dentist@example.test', password, first_name='Mika', last_name='Santos', role='dentist', email_verified=True)
        User.objects.create_user('demo.reception', 'reception@example.test', password, first_name='Jamie', last_name='Reyes', role='reception', email_verified=True)
        user = User.objects.create_user('demo.patient', 'patient@example.test', password, first_name='Sam', last_name='Cruz', email_verified=True)
        patient = Patient.objects.create(user=user, first_name='Sam', last_name='Cruz', birth_date='1998-04-12', phone='09000000000', address='Fictional demo address', medical_history='Demo record — no known conditions reported.', allergies='Demo record — none reported.')
        Patient.objects.create(first_name='Taylor', last_name='Garcia', birth_date='2000-08-15', phone='09000000001')
        services = []
        for name, description, price in [
            ('Dental consultation', 'A conversation and examination to understand your dental care needs.', 500),
            ('Cleaning & prevention', 'Professional cleaning and preventive guidance for a healthier smile.', 1200),
            ('Restorative care', 'Assessment and treatment options to restore damaged teeth.', 1800),
            ('Tooth extraction', 'Assessment and removal of a tooth when clinically appropriate.', 1500),
            ('Orthodontic consultation', 'Explore options for tooth alignment and bite concerns.', 800),
            ('Dental imaging', 'Imaging to support your dentist’s assessment and treatment planning.', 600),
        ]:
            service, _ = Service.objects.get_or_create(name=name, defaults={'description': description, 'price': price})
            services.append(service)
        starts = (timezone.now() + timedelta(days=3)).replace(hour=2, minute=0, second=0, microsecond=0)
        Appointment.objects.create(patient=patient, dentist=dentist, service=services[1], starts_at=starts, status='confirmed')
        Appointment.objects.create(patient=patient, service=services[0], starts_at=starts + timedelta(days=30), status='requested')
        Visit.objects.create(patient=patient, dentist=dentist, date=timezone.localdate() - timedelta(days=14), title='Routine checkup & cleaning', findings='Fictional demo: routine assessment completed. Discussed preventive care.', procedures='Professional cleaning.', instructions='Follow the care instructions provided during your visit.', internal_notes='Fictional staff-only demo note.', released=True)
        Visit.objects.create(patient=patient, dentist=dentist, date=timezone.localdate(), title='Draft assessment', findings='Fictional draft — not visible to the patient.', released=False)
        TreatmentPlan.objects.create(patient=patient, title='Preventive care plan', details='Fictional demonstration plan. Review preventive care at your next appointment.', estimated_cost=1200, released=True)
        invoice = Invoice.objects.create(patient=patient, description='Consultation & professional cleaning', amount=1700, issued_at=timezone.localdate() - timedelta(days=14))
        Payment.objects.create(invoice=invoice, amount=1000, method='cash', received_by=admin)
        for number in [11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48]:
            ToothRecord.objects.create(patient=patient, number=number, condition='healthy', updated_by=dentist)
        self.stdout.write(self.style.SUCCESS('Fictional demo created. Accounts: demo.admin, demo.dentist, demo.reception, demo.patient'))
        self.stdout.write(f'One-time generated demo password (all four accounts): {password}')
        self.stdout.write('Save this password locally. It will not be printed again. Do not use demo accounts in production.')
