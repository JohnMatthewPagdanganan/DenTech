from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Service, AuditEvent


@admin.register(User)
class ClinicUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('Clinic access', {'fields': ('role', 'email_verified')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('Clinic access', {'fields': ('email', 'role')}),)
    list_display = ('username', 'email', 'role', 'is_active', 'email_verified')


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'active')


@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target')
    readonly_fields = ('created_at', 'actor', 'action', 'target')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.site_header = 'DenTech administration'
admin.site.site_title = 'DenTech'
