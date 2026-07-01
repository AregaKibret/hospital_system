from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import (
    AnesthesiaRecord, Appointment, Attendance, ClinicalNote, Department, Diagnosis,
    Dispensing, Doctor, DoctorSchedule, Employee, ImagingOrder, InventoryCategory,
    InventoryItem, Invoice, InvoiceItem, LabOrder, LeaveRequest, MedicationOrder,
    Patient, Payment, PharmacyStock, ProcedureOrder, PurchaseOrder, PurchaseOrderItem,
    Queue, TriageAssessment, UserProfile, Visit, VitalSign,
)

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
    list_display = ('queue_number', 'visit', 'status', 'called_at', 'created_at')
    list_filter = ('status',)
    readonly_fields = ('created_at',)
    ordering = ('queue_number',)


@admin.register(TriageAssessment)
class TriageAssessmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'visit', 'severity', 'chief_complaint', 'triaged_by', 'created_at')
    list_filter = ('severity',)
    search_fields = ('visit__patient__first_name', 'visit__patient__last_name', 'chief_complaint')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'note_type', 'visit', 'authored_by', 'created_at')
    list_filter = ('note_type',)
    search_fields = ('visit__patient__first_name', 'visit__patient__last_name', 'content')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ('id', 'description', 'icd_code', 'status', 'visit', 'authored_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('description', 'icd_code', 'visit__patient__first_name')
    readonly_fields = ('created_at',)


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'test_name', 'priority', 'status', 'ordered_by', 'ordered_at')
    list_filter = ('priority', 'status')
    search_fields = ('test_name', 'visit__patient__first_name')
    readonly_fields = ('ordered_at',)
    date_hierarchy = 'ordered_at'


@admin.register(ImagingOrder)
class ImagingOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'imaging_type', 'body_part', 'priority', 'status', 'ordered_by', 'ordered_at')
    list_filter = ('imaging_type', 'priority', 'status')
    search_fields = ('body_part', 'visit__patient__first_name')
    readonly_fields = ('ordered_at',)


@admin.register(MedicationOrder)
class MedicationOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'drug_name', 'dosage', 'route', 'status', 'ordered_by', 'ordered_at')
    list_filter = ('route', 'status')
    search_fields = ('drug_name', 'visit__patient__first_name')
    readonly_fields = ('ordered_at',)


@admin.register(ProcedureOrder)
class ProcedureOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'procedure_type', 'procedure_name', 'status', 'scheduled_date', 'ordered_by')
    list_filter = ('procedure_type', 'status')
    search_fields = ('procedure_name', 'visit__patient__first_name')
    readonly_fields = ('ordered_at',)


@admin.register(VitalSign)
class VitalSignAdmin(admin.ModelAdmin):
    list_display = ('id', 'visit', 'temperature', 'blood_pressure', 'pulse', 'spo2', 'recorded_by', 'recorded_at')
    readonly_fields = ('recorded_at',)
    date_hierarchy = 'recorded_at'

    @admin.display(description='BP')
    def blood_pressure(self, obj):
        return obj.blood_pressure


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


# ── Pharmacy ──────────────────────────────────────────────────────────────────

@admin.register(PharmacyStock)
class PharmacyStockAdmin(admin.ModelAdmin):
    list_display = ('drug_name', 'generic_name', 'dosage_form', 'strength', 'quantity_in_stock', 'unit', 'selling_price', 'expiry_date')
    list_filter = ('dosage_form', 'category')
    search_fields = ('drug_name', 'generic_name', 'category')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Dispensing)
class DispensingAdmin(admin.ModelAdmin):
    list_display = ('drug_name', 'patient', 'quantity_dispensed', 'total_amount', 'status', 'dispensed_by', 'dispensed_at')
    list_filter = ('status',)
    search_fields = ('drug_name', 'patient__first_name', 'patient__last_name')
    readonly_fields = ('dispensed_at',)
    date_hierarchy = 'dispensed_at'


# ── Billing ───────────────────────────────────────────────────────────────────

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'patient', 'total_amount', 'paid_amount', 'status', 'created_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('invoice_number', 'patient__first_name', 'patient__last_name', 'patient__card_number')
    readonly_fields = ('invoice_number', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'description', 'service_type', 'quantity', 'unit_price', 'total')
    list_filter = ('service_type',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'payment_method', 'reference_number', 'received_by', 'payment_date')
    list_filter = ('payment_method',)
    search_fields = ('invoice__invoice_number', 'reference_number')
    readonly_fields = ('created_at',)
    date_hierarchy = 'payment_date'


# ── Inventory ─────────────────────────────────────────────────────────────────

@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'sku', 'quantity_in_stock', 'unit', 'unit_cost', 'reorder_level')
    list_filter = ('category',)
    search_fields = ('name', 'sku', 'supplier_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('po_number', 'supplier_name', 'status', 'total_amount', 'created_by', 'ordered_at')
    list_filter = ('status',)
    search_fields = ('po_number', 'supplier_name')
    readonly_fields = ('po_number', 'ordered_at')
    date_hierarchy = 'ordered_at'


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(admin.ModelAdmin):
    list_display = ('purchase_order', 'item_name', 'quantity_ordered', 'quantity_received', 'unit_cost', 'total')


# ── Human Resources ───────────────────────────────────────────────────────────

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'department', 'position', 'employment_type', 'employment_status', 'hire_date')
    list_filter = ('department', 'employment_type', 'employment_status')
    search_fields = ('user__first_name', 'user__last_name', 'user__username', 'position', 'national_id')
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='Name')
    def full_name(self, obj):
        return obj.full_name


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'time_in', 'time_out', 'status', 'hours_worked')
    list_filter = ('status', 'date')
    search_fields = ('employee__user__first_name', 'employee__user__last_name')
    date_hierarchy = 'date'

    @admin.display(description='Hours')
    def hours_worked(self, obj):
        return obj.hours_worked


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'days_requested', 'status', 'requested_at')
    list_filter = ('leave_type', 'status')
    search_fields = ('employee__user__first_name', 'employee__user__last_name')
    readonly_fields = ('requested_at',)
    date_hierarchy = 'requested_at'


# ── Anesthesia ────────────────────────────────────────────────────────────────

@admin.register(AnesthesiaRecord)
class AnesthesiaRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'visit', 'anesthesia_type', 'asa_classification', 'duration_minutes', 'anesthesiologist', 'created_at')
    list_filter = ('anesthesia_type', 'asa_classification')
    search_fields = ('visit__patient__first_name', 'visit__patient__last_name')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


# ── Receptionist / Appointments ───────────────────────────────────────────────

@admin.register(DoctorSchedule)
class DoctorScheduleAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'get_day_name', 'start_time', 'end_time', 'slot_duration_minutes', 'max_appointments', 'is_active')
    list_filter = ('doctor', 'is_active', 'day_of_week')
    list_editable = ('is_active',)
    search_fields = ('doctor__first_name', 'doctor__last_name')

    @admin.display(description='Day')
    def get_day_name(self, obj):
        return obj.get_day_of_week_display()


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('appointment_number', 'patient', 'doctor', 'appointment_date', 'appointment_time', 'appointment_type', 'status', 'created_at')
    list_filter = ('status', 'appointment_type', 'appointment_date', 'department')
    search_fields = ('appointment_number', 'patient__first_name', 'patient__last_name', 'patient__card_number')
    readonly_fields = ('appointment_number', 'created_at', 'updated_at', 'created_by')
    autocomplete_fields = ('patient', 'doctor', 'department')
    date_hierarchy = 'appointment_date'
    ordering = ('-appointment_date', '-appointment_time')
