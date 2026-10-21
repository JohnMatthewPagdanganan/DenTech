from django.contrib.auth import views as auth
from django.urls import path
from . import views
from .auth_views import LimitedPasswordResetView

urlpatterns = [
    path('', views.home, name='home'),
    path('info/<str:page>/', views.public_page, name='public_page'),
    path('login/', views.sign_in, name='login'), path('logout/', views.sign_out, name='logout'),
    path('activate/', views.activate, name='activate'), path('activate/setup/', views.account_setup, name='account_setup'),
    path('verify/pending/', views.verification_pending, name='verification_pending'),
    path('verify/<int:user_id>/<str:token>/', views.verify_email, name='verify_email'),
    path('reset/', LimitedPasswordResetView.as_view(template_name='auth/reset.html', email_template_name='auth/reset_email.txt', subject_template_name='auth/reset_subject.txt'), name='password_reset'),
    path('reset/sent/', auth.PasswordResetDoneView.as_view(template_name='auth/reset_sent.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth.PasswordResetConfirmView.as_view(template_name='auth/reset_confirm.html'), name='password_reset_confirm'),
    path('reset/complete/', auth.PasswordResetCompleteView.as_view(template_name='auth/reset_complete.html'), name='password_reset_complete'),
    path('password/', auth.PasswordChangeView.as_view(template_name='form.html', extra_context={'title': 'Change password'}), name='password_change'),
    path('password/complete/', auth.PasswordChangeDoneView.as_view(template_name='auth/password_changed.html'), name='password_change_done'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('patients/', views.patients, name='patients'), path('patients/new/', views.patient_create, name='patient_create'),
    path('patients/<int:pk>/', views.patient_detail, name='patient_detail'),
    path('patients/<int:pk>/edit/', views.patient_edit, name='patient_edit'),
    path('patients/<int:pk>/activation/', views.activation_issue, name='activation_issue'),
    path('patients/<int:pk>/clinical/<str:kind>/', views.clinical_form, name='clinical_create'),
    path('patients/<int:pk>/clinical/<str:kind>/<int:item_id>/', views.clinical_form, name='clinical_edit'),
    path('patients/<int:pk>/invoice/', views.invoice_create, name='invoice_create'),
    path('appointments/', views.appointments, name='appointments'),
    path('appointments/calendar/', views.appointment_calendar, name='appointment_calendar'),
    path('appointments/new/', views.appointment_create, name='appointment_create'),
    path('appointments/<int:pk>/manage/', views.appointment_update, name='appointment_update'),
    path('appointments/<int:pk>/cancel/', views.appointment_cancel, name='appointment_cancel'),
    path('visits/', views.visits, name='visits'), path('visits/<int:pk>/', views.visit_detail, name='visit_detail'),
    path('visits/<int:pk>/upload/', views.document_upload, name='document_upload'),
    path('documents/', views.documents, name='documents'), path('documents/<int:pk>/', views.document_download, name='document_download'),
    path('plans/', views.plans, name='plans'), path('billing/', views.billing, name='billing'),
    path('billing/<int:pk>/', views.receipt, name='receipt'), path('billing/<int:pk>/payment/', views.payment_create, name='payment_create'),
    path('profile/', views.profile, name='profile'), path('reports/', views.reports, name='reports'),
    path('settings/', views.clinic_settings, name='clinic_settings'),
]
