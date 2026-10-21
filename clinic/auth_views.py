from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth.views import PasswordResetView
from django.urls import reverse_lazy
from .services import throttled


class LimitedPasswordResetView(PasswordResetView):
    success_url = reverse_lazy('password_reset_done')

    def form_valid(self, form):
        if not throttled(self.request, 'password-reset', limit=5):
            base = urlsplit(settings.PUBLIC_BASE_URL)
            form.save(use_https=base.scheme == 'https', domain_override=base.netloc,
                from_email=settings.DEFAULT_FROM_EMAIL, request=self.request,
                email_template_name=self.email_template_name, subject_template_name=self.subject_template_name)
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(self.get_success_url())
