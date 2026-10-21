import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import AccessAttempt, ActivationCode, AuditEvent, Patient, Verification


def audit(actor, action, target):
    AuditEvent.objects.create(actor=actor if actor and actor.is_authenticated else None,
                              action=action, target=str(target))


def throttled(request, scope, identifier='', limit=10):
    # Do not trust forwarded IP headers; configure the reverse proxy to preserve REMOTE_ADDR.
    raw = f'{scope}:{request.META.get("REMOTE_ADDR", "")}:{identifier.lower()}'
    key = hashlib.sha256(raw.encode()).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        attempt, _ = AccessAttempt.objects.select_for_update().get_or_create(key=key, defaults={'window_start': now})
        if attempt.window_start < now - timedelta(minutes=15):
            attempt.count = 0
            attempt.window_start = now
        attempt.count += 1
        attempt.save()
        return attempt.count > limit


def issue_activation(patient, actor):
    with transaction.atomic():
        patient = Patient.objects.select_for_update().get(pk=patient.pk)
        if patient.user_id:
            raise ValueError('This patient already has a portal account.')
        ActivationCode.objects.filter(patient=patient, used_at=None).update(used_at=timezone.now())
        code = secrets.token_hex(6).upper()
        ActivationCode.objects.create(patient=patient, code_hash=make_password(code),
                                      expires_at=timezone.now() + timedelta(hours=48))
        audit(actor, 'Issued activation code', patient.pid)
    return code


def validate_activation(pid, code):
    # Wrong attempts are committed even when activation is rejected.
    with transaction.atomic():
        token = ActivationCode.objects.select_for_update().filter(patient__pid=pid, used_at=None).order_by('-pk').first()
        if not token or token.expires_at <= timezone.now() or token.attempts >= 5 or token.patient.user_id:
            return None
        ActivationCode.objects.filter(pk=token.pk).update(attempts=F('attempts') + 1)
        return token if check_password(code.strip().upper(), token.code_hash) else None


def prepare_verification(user):
    token = secrets.token_urlsafe(32)
    Verification.objects.update_or_create(user=user, defaults={
        'token_hash': hashlib.sha256(token.encode()).hexdigest(),
        'expires_at': timezone.now() + timedelta(hours=24), 'used_at': None})
    return token


def send_verification(user, token):
    link = f'{settings.PUBLIC_BASE_URL}/verify/{user.pk}/{token}/'
    send_mail('Verify your DenTech email',
        f'Hello {user.first_name},\n\nConfirm your email to activate your patient portal:\n{link}\n\n'
        'This link expires in 24 hours. If you did not request this, contact your clinic.',
        settings.DEFAULT_FROM_EMAIL, [user.email])
