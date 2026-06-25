from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import Department, Doctor, Patient, Queue, UserProfile, Visit

User = get_user_model()


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'first_name', 'last_name', 'department', 'mobile', 'active')
    list_filter = ('department', 'active')
    list_editable = ('active',)
    search_fields = ('first_name', 'last_name', 'employee_id')
    autocomplete_fields = ('department',)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('card_number', 'first_name', 'last_name', 'sex', 'mobile', 'created_at')
    list_filter = ('sex', 'region')
    search_fields = ('first_name', 'last_name', 'card_number', 'mobile')
    readonly_fields = ('card_number', 'created_at')
    fieldsets = (
        ('Identity', {
            'fields': ('card_number', 'first_name', 'middle_name', 'last_name', 'sex', 'date_of_birth'),
        }),
        ('Contact', {
            'fields': ('mobile', 'emergency_contact'),
        }),
        ('Address', {
            'fields': ('nationality', 'region', 'city', 'subcity', 'wereda', 'house_no'),
        }),
        ('Other', {
            'fields': ('occupation', 'education', 'created_at'),
        }),
    )


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ('id', 'patient', 'department', 'doctor', 'visit_type', 'created_at')
    list_filter = ('department', 'visit_type')
    search_fields = ('patient__first_name', 'patient__last_name', 'patient__card_number')
    autocomplete_fields = ('patient', 'doctor', 'department')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):
    list_display = ('queue_number', 'visit', 'created_at')
    readonly_fields = ('created_at',)
    ordering = ('queue_number',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'employee_id', 'department', 'phone', 'role_name')
    list_filter = ('department',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'employee_id')
    autocomplete_fields = ('department',)
    readonly_fields = ('updated_at', 'role_name')

    @admin.display(description='Role')
    def role_name(self, obj):
        return obj.role_name
