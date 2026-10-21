def navigation(request):
    from .models import ClinicSettings
    return {'is_clinic_staff': request.user.is_authenticated and request.user.clinic_staff,
            'has_clinical_access': request.user.is_authenticated and request.user.clinical_access,
            'clinic_settings': ClinicSettings.objects.first()}
