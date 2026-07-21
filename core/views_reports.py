"""
HMS Reporting Module — central views for all management reports.
Every view supports HTML viewing, Excel export (?export=excel),
CSV export (?export=csv), and browser-print/PDF (window.print()).
"""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Avg, Case, Count, DecimalField, F, Q, Sum, When
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.shortcuts import render
from django.utils import timezone

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    Admission,
    AdmissionDepositRule,
    AttachmentCategory,
    PatientAttachment,
    AdmissionRequest,
    Appointment,
    AuditLog,
    Attendance,
    Bed,
    CardType,
    ClinicalNote,
    ConsultationType,
    Department,
    DepartmentStore,
    Diagnosis,
    Doctor,
    Employee,
    ImagingOrder,
    InventoryAdjustment,
    InventoryCategory,
    InventoryItem,
    InventoryPeriod,
    InventoryPeriodBalance,
    Invoice,
    InvoiceItem,
    LabOrder,
    Medication,
    MedicationOrder,
    ORRoom,
    Patient,
    PatientCard,
    Payment,
    PharmacySale,
    PharmacySaleItem,
    PhysicalCount,
    PhysicalCountLine,
    PrescriptionItem,
    ProcedureOrder,
    Prescription,
    Room,
    Specialization,
    SurgeryOrder,
    Visit,
    Ward,
)
from .utils_export import (
    build_workbook,
    csv_response,
    excel_response,
    send_workbook,
    write_csv,
)

User = get_user_model()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date_range(request):
    today = timezone.localdate()
    first_of_month = today.replace(day=1)
    from_str = request.GET.get('from_date', '')
    to_str   = request.GET.get('to_date', '')
    try:
        from_date = datetime.strptime(from_str, '%Y-%m-%d').date() if from_str else first_of_month
    except ValueError:
        from_date = first_of_month
    try:
        to_date = datetime.strptime(to_str, '%Y-%m-%d').date() if to_str else today
    except ValueError:
        to_date = today
    return from_date, to_date, from_str or from_date.strftime('%Y-%m-%d'), to_str or to_date.strftime('%Y-%m-%d')


def _age_cutoffs(age_group: str):
    """Return (min_dob, max_dob) for a named age group, or (None, None)."""
    today = timezone.localdate()

    def _replace_year(y):
        try:
            return today.replace(year=y)
        except ValueError:
            return today.replace(year=y, day=28)

    if age_group == '0-18':
        return _replace_year(today.year - 18), None
    if age_group == '19-40':
        return _replace_year(today.year - 40), _replace_year(today.year - 19)
    if age_group == '41-60':
        return _replace_year(today.year - 60), _replace_year(today.year - 41)
    if age_group == '61+':
        return None, _replace_year(today.year - 61)
    return None, None


def _qp(request, exclude=('page', 'export')):
    """Build a query string from GET params, excluding specified keys."""
    return '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in exclude)


def _period_label(from_date, to_date):
    return f"{from_date:%d %b %Y} – {to_date:%d %b %Y}"


def _generated_by(request):
    return request.user.get_full_name() or request.user.username


# ── Reports Hub ───────────────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def reports_hub(request):
    today = timezone.localdate()
    first_of_month = today.replace(day=1)

    stats = {
        'today_visits':   Visit.objects.filter(created_at__date=today).count(),
        'today_patients': Patient.objects.filter(created_at__date=today).count(),
        'today_lab':      LabOrder.objects.filter(ordered_at__date=today).count(),
        'today_imaging':  ImagingOrder.objects.filter(ordered_at__date=today).count(),
        'month_patients': Patient.objects.filter(created_at__date__gte=first_of_month).count(),
        'month_visits':   Visit.objects.filter(created_at__date__gte=first_of_month).count(),
        'month_revenue':  Payment.objects.filter(payment_date__gte=first_of_month).aggregate(t=Sum('amount'))['t'] or 0,
    }
    can_financial = request.user.has_perm('core.read_financial_report')
    can_dept      = request.user.has_perm('core.read_department_reports')
    can_audit     = request.user.has_perm('core.read_audit_log')
    can_hr        = request.user.has_perm('core.manage_employees')
    can_facility  = request.user.has_perm('core.view_facility_reports')
    can_admission = request.user.has_perm('core.view_admission_reports')
    can_cards     = request.user.has_perm('core.view_card_reports')
    can_specializations = request.user.has_perm('core.view_specialization_reports')
    can_physical_count = request.user.has_perm('core.view_store_reports')
    can_attachments = request.user.has_perm('core.view_attachment_reports')
    facility_reports = [
        ('report_bed_occupancy', 'Bed Occupancy', 'Current bed status across all wards'),
        ('report_ward_occupancy', 'Ward Occupancy', 'Occupancy rate per ward'),
        ('report_room_utilization', 'Room Utilization', 'Beds occupied per room'),
        ('report_or_utilization', 'OR Utilization', 'Surgeries vs. capacity by OR'),
        ('report_admission_discharge', 'Admission & Discharge', 'Admissions/discharges in a period'),
        ('report_patient_transfer', 'Patient Transfer', 'Bed transfers in a period'),
        ('report_facility_maintenance', 'Facility Maintenance', 'Beds/rooms/wards/ORs under maintenance'),
        ('report_available_beds', 'Available Beds', 'Currently available beds'),
        ('report_room_status', 'Room Status', 'Status of every room'),
    ]
    admission_reports = [
        ('report_admission_register', 'Admission Register', 'Every admission request in a period'),
        ('report_daily_admissions', 'Daily Admission Report', 'Admissions by day'),
        ('report_length_of_stay', 'Length of Stay Report', 'Days admitted per discharged patient'),
        ('report_deposit_collection', 'Deposit Collection Report', 'Admission deposits by status'),
        ('report_admission_revenue', 'Admission Revenue Report', 'Charges billed on inpatient visits'),
        ('report_department_admission_stats', 'Department Admission Statistics', 'Admissions per department'),
        ('report_readmissions', 'Readmission Report', 'Patients readmitted within a lookback window'),
        ('report_admission_cancellations', 'Admission Cancellation Report', 'Rejected/cancelled admission requests'),
    ]
    card_reports = [
        ('report_card_type_usage', 'Card Type Usage', 'Issued/renewed counts per card type'),
        ('report_card_renewal', 'Card Renewal', 'Cards renewed in a period'),
        ('report_expired_cards', 'Expired Cards', 'Cards currently expired'),
        ('report_consultation_type', 'Consultation Type', 'Visit counts per consultation type'),
        ('report_revenue_by_card_type', 'Revenue by Card Type', 'Charged vs. collected per card type'),
        ('report_revenue_by_consultation_type', 'Revenue by Consultation Type', 'Charged vs. collected per consultation type'),
        ('report_department_consultation_stats', 'Department Consultation Stats', 'Consultations per department'),
    ]
    specialization_reports = [
        ('report_doctors_by_department', 'Doctors by Department', 'Doctor headcount per department'),
        ('report_doctors_by_specialization', 'Doctors by Specialization', 'Doctor headcount per specialization'),
        ('report_patient_volume_by_specialization', 'Patient Volume by Specialization', 'Unique patients & visits per specialization'),
        ('report_revenue_by_specialization', 'Revenue by Specialization', 'Charged vs. collected per specialization'),
        ('report_appointment_stats_by_specialization', 'Appointment Statistics by Specialization', 'Completed/cancelled/no-show per specialization'),
    ]
    physical_count_reports = [
        ('report_physical_count', 'Physical Inventory Count Report', 'Every counted line across all counts'),
        ('report_inventory_reconciliation', 'Inventory Reconciliation Report', 'Per-count summary of variances & adjustment value'),
        ('report_inventory_adjustments', 'Inventory Adjustment Report', 'Every stock adjustment posted from a count'),
        ('report_opening_balance', 'Opening Balance Report', 'Opening stock & value per inventory period'),
        ('report_closing_balance', 'Closing Balance Report', 'Closing stock & value per inventory period'),
        ('report_annual_inventory', 'Annual Inventory Report', 'Opening vs. closing comparison for a period'),
        ('report_stock_variance', 'Stock Variance Report', 'All counted lines with a quantity variance'),
        ('report_inventory_valuation_combined', 'Inventory Valuation Report', 'Current stock valuation across all domains'),
        ('report_inventory_count_history', 'Inventory Count History', 'Every physical count session'),
        ('report_inventory_audit', 'Inventory Audit Report', 'Audit trail of inventory actions'),
    ]
    attachment_reports = [
        ('report_attachment_history', 'Patient Attachment History', 'Every document uploaded, filterable by patient/date'),
        ('report_attachments_by_category', 'Attachments by Category', 'Document counts per category'),
        ('report_attachments_by_department', 'Attachments by Department', 'Document counts per department'),
        ('report_missing_required_documents', 'Missing Required Documents', 'Admitted patients missing a required document category'),
        ('report_recent_attachments', 'Recently Uploaded Documents', 'Latest 200 uploads in a period'),
        ('report_confidential_attachments', 'Confidential Documents', 'All documents marked confidential'),
        ('report_attachment_activity_log', 'Attachment Activity Log', 'Full upload/replace/delete/restore audit trail'),
    ]

    deposit_reports = [
        ('report_deposit_collection',    'Deposit Collection',       'All admission deposits collected in a period'),
        ('report_deposit_balance',       'Deposit Balance Report',   'Current balance for each inpatient account'),
        ('report_low_balance',           'Low Balance Alert',        'Accounts with low or exhausted deposit balances'),
        ('report_deposit_transactions',  'Deposit Transaction History', 'Full ledger of every deposit transaction'),
        ('report_discharge_settlement',  'Discharge Settlement',     'Final reconciliation summaries at discharge'),
    ]

    return render(request, 'reports/hub.html', {
        'today': today,
        'stats': stats,
        'can_financial': can_financial,
        'can_dept': can_dept,
        'can_audit': can_audit,
        'can_hr': can_hr,
        'can_facility': can_facility,
        'facility_reports': facility_reports,
        'can_admission': can_admission,
        'admission_reports': admission_reports,
        'deposit_reports': deposit_reports,
        'can_cards': can_cards,
        'card_reports': card_reports,
        'can_specializations': can_specializations,
        'specialization_reports': specialization_reports,
        'can_physical_count': can_physical_count,
        'physical_count_reports': physical_count_reports,
        'can_attachments': can_attachments,
        'attachment_reports': attachment_reports,
    })


# Keep old name as alias so existing URL name 'reports_dashboard' still resolves
reports_dashboard = reports_hub


# ── Executive Overview ────────────────────────────────────────────────────────

@hms_permission_required('core.read_financial_report')
def report_executive(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    export      = request.GET.get('export', '')

    visits_qs   = Visit.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    invoices_qs = Invoice.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    payments_qs = Payment.objects.filter(payment_date__gte=from_date, payment_date__lte=to_date)
    patients_qs = Patient.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)

    if dept_filter:
        visits_qs = visits_qs.filter(department__id=dept_filter)

    total_revenue      = payments_qs.aggregate(t=Sum('amount'))['t'] or 0
    total_invoiced     = invoices_qs.aggregate(t=Sum('total_amount'))['t'] or 0
    outstanding_amount = Invoice.objects.filter(
        status__in=['Draft', 'Issued', 'Partial', 'Credit Pending']
    ).aggregate(t=Sum(F('total_amount') - F('paid_amount') - F('discount')))['t'] or 0

    pharmacy_revenue = PharmacySale.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
        status__in=[PharmacySale.Status.DISPENSED, PharmacySale.Status.CREDIT],
    ).aggregate(t=Sum('total_amount'))['t'] or 0

    new_patients      = patients_qs.count()
    total_visits      = visits_qs.count()
    total_lab         = LabOrder.objects.filter(ordered_at__date__gte=from_date, ordered_at__date__lte=to_date).count()
    total_imaging     = ImagingOrder.objects.filter(ordered_at__date__gte=from_date, ordered_at__date__lte=to_date).count()
    total_prescriptions = Prescription.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date).count()

    revenue_by_service = (
        InvoiceItem.objects.filter(invoice__in=invoices_qs)
        .values('service_type')
        .annotate(total=Sum('total'), count=Count('id'))
        .order_by('-total')
    )

    dept_visits = (
        visits_qs.values('department__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    max_dept = dept_visits[0]['count'] if dept_visits else 1
    for d in dept_visits:
        d['pct'] = round(d['count'] / max_dept * 100) if max_dept else 0

    visit_types = (
        visits_qs.values('visit_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    daily_revenue = (
        payments_qs.values('payment_date')
        .annotate(collected=Sum('amount'))
        .order_by('payment_date')
    )

    departments = Department.objects.filter(is_active=True).order_by('name')

    kpis = {
        'new_patients': new_patients,
        'total_visits': total_visits,
        'total_revenue': total_revenue,
        'total_invoiced': total_invoiced,
        'outstanding': outstanding_amount,
        'pharmacy_revenue': pharmacy_revenue,
        'total_lab': total_lab,
        'total_imaging': total_imaging,
        'total_prescriptions': total_prescriptions,
        'collection_rate': round(float(total_revenue) / float(total_invoiced) * 100, 1) if total_invoiced else 0,
    }

    return render(request, 'reports/executive.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'dept_filter': dept_filter,
        'departments': departments,
        'kpis': kpis,
        'revenue_by_service': revenue_by_service,
        'dept_visits': dept_visits,
        'visit_types': visit_types,
        'daily_revenue': list(daily_revenue),
        'qp': _qp(request),
    })


# ── Patient Registration Report ───────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_patient_registration(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    gender    = request.GET.get('gender', '')
    age_group = request.GET.get('age_group', '')
    q         = request.GET.get('q', '').strip()
    export    = request.GET.get('export', '')

    qs = Patient.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).order_by('-created_at')

    if gender:
        qs = qs.filter(sex=gender)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) |
            Q(middle_name__icontains=q) | Q(card_number__icontains=q) |
            Q(mobile__icontains=q)
        )
    if age_group:
        min_dob, max_dob = _age_cutoffs(age_group)
        if min_dob:
            qs = qs.filter(date_of_birth__gte=min_dob)
        if max_dob:
            qs = qs.filter(date_of_birth__lt=max_dob)

    total = qs.count()
    today = timezone.localdate()

    if export in ('excel', 'csv'):
        headers = ['MRN', 'Full Name', 'Gender', 'Date of Birth', 'Age', 'Phone', 'Region', 'Registered At']
        rows = []
        for p in qs.values('card_number', 'first_name', 'middle_name', 'last_name',
                           'sex', 'date_of_birth', 'mobile', 'region', 'created_at'):
            dob = p['date_of_birth']
            age = str((today - dob).days // 365) if dob else '—'
            full = f"{p['first_name']} {p['middle_name'] or ''} {p['last_name']}".strip()
            rows.append([
                p['card_number'], full, p['sex'],
                dob.strftime('%d %b %Y') if dob else '—', age,
                p['mobile'], p['region'] or '—',
                timezone.localtime(p['created_at']).strftime('%d %b %Y %H:%M')
                if p['created_at'] else '—',
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'patients_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Patient Registration Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'patients_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    # Summary
    gender_summary = qs.values('sex').annotate(count=Count('id')).order_by('-count')
    weekly = (
        qs.annotate(week=TruncWeek('created_at'))
        .values('week').annotate(count=Count('id')).order_by('week')
    )

    return render(request, 'reports/patient_registration.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'gender': gender, 'age_group': age_group, 'q': q,
        'total': total, 'page_obj': page_obj,
        'gender_summary': gender_summary, 'weekly': weekly,
        'qp': _qp(request),
    })


# ── Patient Visit Report ──────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_patient_visits(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    visit_type  = request.GET.get('visit_type', '')
    dept_filter = request.GET.get('department', '')
    status_f    = request.GET.get('status', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    qs = Visit.objects.select_related(
        'patient', 'department', 'doctor'
    ).filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).order_by('-created_at')

    if visit_type:
        qs = qs.filter(visit_type=visit_type)
    if dept_filter:
        qs = qs.filter(department__id=dept_filter)
    if status_f:
        qs = qs.filter(status=status_f)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q) | Q(visit_number__icontains=q)
        )

    total = qs.count()

    if export in ('excel', 'csv'):
        headers = ['Visit #', 'Patient', 'MRN', 'Date', 'Visit Type', 'Department', 'Doctor', 'Status', 'Payment']
        rows = []
        for v in qs.values(
            'visit_number', 'patient__first_name', 'patient__last_name',
            'patient__card_number', 'created_at', 'visit_type',
            'department__name', 'doctor__first_name', 'doctor__last_name',
            'status',
        ):
            rows.append([
                v['visit_number'],
                f"{v['patient__first_name']} {v['patient__last_name']}",
                v['patient__card_number'],
                timezone.localtime(v['created_at']).strftime('%d %b %Y %H:%M'),
                v['visit_type'],
                v['department__name'] or '—',
                f"Dr. {v['doctor__first_name']} {v['doctor__last_name']}"
                if v['doctor__first_name'] else '—',
                v['status'] or '—',
                '—',
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'visits_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Patient Visit Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'visits_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    type_summary = qs.values('visit_type').annotate(count=Count('id')).order_by('-count')
    dept_summary = qs.values('department__name').annotate(count=Count('id')).order_by('-count')[:8]
    departments  = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/patient_visits.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'visit_type': visit_type, 'dept_filter': dept_filter,
        'status_f': status_f, 'q': q,
        'total': total, 'page_obj': page_obj,
        'type_summary': type_summary, 'dept_summary': dept_summary,
        'departments': departments,
        'visit_statuses': Visit.Status.choices,
        'qp': _qp(request),
    })


# ── Doctor Activity / Income Report ──────────────────────────────────────────

@hms_permission_required('core.read_department_reports')
def report_doctor_activity(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    doctor_id   = request.GET.get('doctor', '')
    dept_filter = request.GET.get('department', '')
    export      = request.GET.get('export', '')

    all_doctors = Doctor.objects.filter(active=True).select_related('department').order_by('first_name', 'last_name')
    departments = Department.objects.filter(is_active=True).order_by('name')

    date_range = Q(
        invoice__visit__created_at__date__gte=from_date,
        invoice__visit__created_at__date__lte=to_date,
    )
    exclude_statuses = [InvoiceItem.PaymentStatus.CANCELLED, InvoiceItem.PaymentStatus.REFUNDED]

    # Resolve which doctors to show
    doctors_qs = Doctor.objects.filter(active=True).select_related('department')
    if dept_filter:
        doctors_qs = doctors_qs.filter(department__id=dept_filter)
    if doctor_id:
        doctors_qs = doctors_qs.filter(id=doctor_id)

    # Service type choices for display
    service_types = [st.value for st in InvoiceItem.ServiceType]

    rows = []
    for doc in doctors_qs.order_by('first_name', 'last_name'):
        items_qs = InvoiceItem.objects.filter(
            date_range,
            invoice__visit__doctor=doc,
        ).exclude(payment_status__in=exclude_statuses)

        if not items_qs.exists():
            continue

        # Aggregate total and per-service-type
        totals = items_qs.aggregate(
            grand_total=Sum('total'),
            paid=Sum('paid_amount'),
        )

        by_type = {}
        for st in InvoiceItem.ServiceType:
            agg = items_qs.filter(service_type=st.value).aggregate(
                amount=Sum('total'), count=Count('id')
            )
            if agg['amount']:
                by_type[st.label] = {'amount': agg['amount'], 'count': agg['count']}

        rows.append({
            'doctor': doc,
            'grand_total': totals['grand_total'] or 0,
            'paid': totals['paid'] or 0,
            'by_type': by_type,
            'visit_count': items_qs.values('invoice__visit').distinct().count(),
        })

    rows.sort(key=lambda r: r['grand_total'], reverse=True)

    if export in ('excel', 'csv'):
        headers = ['Doctor', 'Department', 'Visits', 'Grand Total (ETB)', 'Paid (ETB)',
                   'Consultation', 'Laboratory', 'Imaging', 'Medication',
                   'Procedure', 'Surgery', 'Bed/Room', 'Nursing', 'Other']

        def _get(r, label):
            return r['by_type'].get(label, {}).get('amount') or 0

        export_rows = [
            [
                f"Dr. {r['doctor'].first_name} {r['doctor'].last_name}",
                r['doctor'].department.name if r['doctor'].department else '—',
                r['visit_count'],
                r['grand_total'], r['paid'],
                _get(r, 'Consultation'), _get(r, 'Laboratory'), _get(r, 'Imaging'),
                _get(r, 'Medication'), _get(r, 'Procedure'),
                _get(r, 'Surgery Booking Deposit') + _get(r, 'Surgery Pre-Deposit'),
                _get(r, 'Bed / Room'), _get(r, 'Nursing Care'), _get(r, 'Other'),
            ]
            for r in rows
        ]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'doctor_activity_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Doctor Activity Report', period, _generated_by(request), headers, export_rows)
            return send_workbook(wb, r)
        r = csv_response(f'doctor_activity_{from_str}_{to_str}.csv')
        return write_csv(r, headers, export_rows)

    # Summary totals
    grand_total = sum(r['grand_total'] for r in rows)
    grand_paid  = sum(r['paid'] for r in rows)

    return render(request, 'reports/doctor_activity.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'doctor_id': doctor_id, 'dept_filter': dept_filter,
        'all_doctors': all_doctors, 'departments': departments,
        'rows': rows,
        'grand_total': grand_total, 'grand_paid': grand_paid,
        'qp': _qp(request),
    })


# ── Doctor Performance Report ─────────────────────────────────────────────────

@hms_permission_required('core.read_department_reports')
def report_doctor_performance(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    doctor_id   = request.GET.get('doctor', '')
    export      = request.GET.get('export', '')

    # Base queryset — no date filter here; dates go into annotations only
    doctors_qs = Doctor.objects.select_related('department').filter(active=True)

    if dept_filter:
        doctors_qs = doctors_qs.filter(department__id=dept_filter)
    if doctor_id:
        doctors_qs = doctors_qs.filter(id=doctor_id)

    date_q = Q(visits__created_at__date__gte=from_date,
                visits__created_at__date__lte=to_date)

    doctors_qs = doctors_qs.annotate(
        total_visits=Count('visits', distinct=True, filter=date_q),
        new_patients=Count('visits__patient', distinct=True,
                           filter=date_q & Q(visits__visit_type=Visit.VisitType.NEW_VISIT)),
        revisits=Count('visits', distinct=True,
                       filter=date_q & Q(visits__visit_type=Visit.VisitType.REVISIT)),
        notes_written=Count('visits__clinical_notes', distinct=True, filter=date_q),
        diagnoses_made=Count('visits__diagnoses', distinct=True, filter=date_q),
    ).filter(total_visits__gt=0).order_by('-total_visits')

    # If a specific doctor is selected, show them even with 0 visits
    if doctor_id:
        doctors_qs = Doctor.objects.select_related('department').filter(id=doctor_id).annotate(
            total_visits=Count('visits', distinct=True, filter=date_q),
            new_patients=Count('visits__patient', distinct=True,
                               filter=date_q & Q(visits__visit_type=Visit.VisitType.NEW_VISIT)),
            revisits=Count('visits', distinct=True,
                           filter=date_q & Q(visits__visit_type=Visit.VisitType.REVISIT)),
            notes_written=Count('visits__clinical_notes', distinct=True, filter=date_q),
            diagnoses_made=Count('visits__diagnoses', distinct=True, filter=date_q),
        ).order_by('-total_visits')

    if export in ('excel', 'csv'):
        headers = ['Doctor', 'Department', 'Total Visits', 'New Patients',
                   'Revisits', 'Notes Written', 'Diagnoses Made']
        rows = [
            [f"Dr. {d.first_name} {d.last_name}", d.department.name if d.department else '—',
             d.total_visits, d.new_patients, d.revisits, d.notes_written, d.diagnoses_made]
            for d in doctors_qs
        ]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'doctor_performance_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Doctor Performance Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'doctor_performance_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator   = Paginator(list(doctors_qs), 30)
    page_obj    = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')
    all_doctors = Doctor.objects.filter(active=True).order_by('first_name', 'last_name')

    return render(request, 'reports/doctor_performance.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'dept_filter': dept_filter, 'doctor_id': doctor_id,
        'page_obj': page_obj, 'departments': departments,
        'all_doctors': all_doctors,
        'total_doctors': doctors_qs.count(),
        'qp': _qp(request),
    })


# ── Diagnosis Report ──────────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_diagnosis(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    qs = Diagnosis.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    )
    if dept_filter:
        qs = qs.filter(visit__department__id=dept_filter)
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(icd_code__icontains=q))

    summary = (
        qs.values('description', 'icd_code')
        .annotate(
            total_cases=Count('id'),
            unique_patients=Count('visit__patient', distinct=True),
        )
        .order_by('-total_cases')
    )
    if q:
        summary = summary.filter(Q(description__icontains=q) | Q(icd_code__icontains=q))

    total_diagnoses = qs.count()
    max_count = summary[0]['total_cases'] if summary else 1
    for row in summary:
        row['pct'] = round(row['total_cases'] / max_count * 100) if max_count else 0

    if export in ('excel', 'csv'):
        headers = ['Diagnosis', 'ICD Code', 'Total Cases', 'Unique Patients', '% of Max']
        rows = [
            [r['description'], r['icd_code'] or '—', r['total_cases'],
             r['unique_patients'], f"{r['pct']}%"]
            for r in summary
        ]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'diagnosis_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Diagnosis Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'diagnosis_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator  = Paginator(list(summary), 50)
    page_obj   = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')

    # Status breakdown
    status_summary = qs.values('status').annotate(count=Count('id')).order_by('-count')

    return render(request, 'reports/diagnosis.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'dept_filter': dept_filter, 'q': q,
        'total_diagnoses': total_diagnoses,
        'page_obj': page_obj,
        'status_summary': status_summary,
        'departments': departments,
        'qp': _qp(request),
    })


# ── Laboratory Activity Report ────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_lab_activity(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f    = request.GET.get('status', '')
    dept_filter = request.GET.get('department', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    qs = LabOrder.objects.filter(
        ordered_at__date__gte=from_date,
        ordered_at__date__lte=to_date,
    ).select_related('visit__department', 'ordered_by')

    if status_f:
        qs = qs.filter(status=status_f)
    if dept_filter:
        qs = qs.filter(visit__department__id=dept_filter)
    if q:
        qs = qs.filter(Q(test_name__icontains=q) | Q(visit__patient__first_name__icontains=q))

    total = qs.count()

    # Test summary
    test_summary = (
        qs.values('test_name')
        .annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status='Pending')),
            in_progress=Count('id', filter=Q(status='In Progress')),
            completed=Count('id', filter=Q(status='Completed')),
            cancelled=Count('id', filter=Q(status='Cancelled')),
        )
        .order_by('-total')
    )

    status_summary = qs.values('status').annotate(count=Count('id')).order_by('-count')

    dept_summary = (
        qs.values('visit__department__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    if export in ('excel', 'csv'):
        headers = ['Test Name', 'Total Orders', 'Pending', 'In Progress', 'Completed', 'Cancelled']
        rows = [
            [r['test_name'], r['total'], r['pending'], r['in_progress'], r['completed'], r['cancelled']]
            for r in test_summary
        ]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'lab_activity_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Laboratory Activity Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'lab_activity_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator   = Paginator(list(test_summary), 50)
    page_obj    = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/lab_activity.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f, 'dept_filter': dept_filter, 'q': q,
        'total': total, 'page_obj': page_obj,
        'status_summary': status_summary,
        'dept_summary': dept_summary,
        'departments': departments,
        'qp': _qp(request),
    })


# ── Radiology Activity Report ─────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_radiology_activity(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f    = request.GET.get('status', '')
    dept_filter = request.GET.get('department', '')
    export      = request.GET.get('export', '')

    qs = ImagingOrder.objects.filter(
        ordered_at__date__gte=from_date,
        ordered_at__date__lte=to_date,
    ).select_related('visit__department', 'ordered_by')

    if status_f:
        qs = qs.filter(status=status_f)
    if dept_filter:
        qs = qs.filter(visit__department__id=dept_filter)

    total = qs.count()

    type_summary = (
        qs.values('imaging_type')
        .annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status='Pending')),
            completed=Count('id', filter=Q(status='Completed')),
        )
        .order_by('-total')
    )

    status_summary = qs.values('status').annotate(count=Count('id')).order_by('-count')
    dept_summary   = qs.values('visit__department__name').annotate(count=Count('id')).order_by('-count')[:10]

    if export in ('excel', 'csv'):
        headers = ['Imaging Type', 'Total Orders', 'Pending', 'Completed']
        rows = [
            [r['imaging_type'], r['total'], r['pending'], r['completed']]
            for r in type_summary
        ]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'radiology_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Radiology Activity Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'radiology_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/radiology_activity.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f, 'dept_filter': dept_filter,
        'total': total,
        'type_summary': list(type_summary),
        'status_summary': status_summary,
        'dept_summary': dept_summary,
        'departments': departments,
        'qp': _qp(request),
    })


# ── Pharmacy Sales Summary ────────────────────────────────────────────────────

@hms_permission_required('core.read_financial_report')
def report_pharmacy_summary(request):
    """Combined pharmacy revenue: walk-in/OTC POS sales (PharmacySale) plus
    medication billed through the Doctor -> Billing -> Pharmacy dispensing
    flow (PrescriptionItem.invoice_item). These are two independent revenue
    streams recorded in different tables — most hospitals using this system
    only ever exercise the prescription flow, so the report must not be
    scoped to PharmacySale alone or it will look empty despite real activity."""
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f = request.GET.get('status', '')
    export   = request.GET.get('export', '')

    # ---- Walk-in / OTC POS sales ----
    pos_qs = PharmacySale.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).select_related('patient', 'cashier')
    if status_f:
        pos_qs = pos_qs.filter(status=status_f)

    pos_totals = pos_qs.aggregate(
        revenue=Sum('total_amount'),
        paid=Sum('paid_amount'),
        discount=Sum('discount_amount'),
        count=Count('id'),
    )

    # ---- Prescription-based dispensing revenue ----
    # Only items whose billing has actually cleared count as revenue — mirrors
    # department-queue gating used elsewhere. Two billing paths exist: newer
    # prescriptions link each item to its own InvoiceItem (item-level
    # tracking); older ones were only ever billed at the whole-prescription
    # level (Prescription.billing_status), with no per-item InvoiceItem link
    # at all — both must count, or paid prescriptions billed the old way
    # invisibly disappear from this report.
    _rx_money = DecimalField(max_digits=12, decimal_places=2)
    rx_items_qs = PrescriptionItem.objects.filter(
        Q(invoice_item__payment_status__in=[InvoiceItem.PaymentStatus.PAID, InvoiceItem.PaymentStatus.CREDIT])
        | Q(invoice_item__isnull=True, prescription__billing_status__in=[
            Prescription.BillingStatus.PAID, Prescription.BillingStatus.CREDIT_APPROVED,
        ]),
        prescription__created_at__date__gte=from_date,
        prescription__created_at__date__lte=to_date,
    ).select_related('invoice_item', 'prescription__patient').annotate(
        _revenue=Case(
            When(invoice_item__isnull=False, then=F('invoice_item__total')),
            default=F('quantity') * F('unit_price'),
            output_field=_rx_money,
        ),
        _paid=Case(
            When(invoice_item__isnull=False, then=F('invoice_item__paid_amount')),
            default=F('quantity') * F('unit_price'),
            output_field=_rx_money,
        ),
    )

    rx_totals = rx_items_qs.aggregate(
        revenue=Sum('_revenue'),
        paid=Sum('_paid'),
        count=Count('prescription_id', distinct=True),
    )

    totals = {
        'revenue': (pos_totals['revenue'] or 0) + (rx_totals['revenue'] or 0),
        'paid': (pos_totals['paid'] or 0) + (rx_totals['paid'] or 0),
        'discount': pos_totals['discount'] or 0,
        'count': (pos_totals['count'] or 0) + (rx_totals['count'] or 0),
    }

    # By payment method — prescription payments are recorded at the invoice
    # level, not per medication item, so they're bucketed under one label
    # rather than guessed at per drug.
    method_totals = {}
    for row in pos_qs.values('payment_method').annotate(total=Sum('total_amount'), count=Count('id')):
        key = row['payment_method'] or 'Unspecified'
        bucket = method_totals.setdefault(key, {'total': Decimal('0.00'), 'count': 0})
        bucket['total'] += row['total'] or 0
        bucket['count'] += row['count']
    if rx_totals['revenue']:
        bucket = method_totals.setdefault('Prescription Billing', {'total': Decimal('0.00'), 'count': 0})
        bucket['total'] += rx_totals['revenue']
        bucket['count'] += rx_totals['count'] or 0
    method_summary = [
        {'payment_method': k, 'total': v['total'], 'count': v['count']}
        for k, v in sorted(method_totals.items(), key=lambda kv: -kv[1]['total'])
    ]

    # Top medications — merge both streams keyed on drug_name, since
    # PrescriptionItem is almost always free-text (no linked Medication).
    med_totals = {}
    for row in (PharmacySaleItem.objects.filter(sale__in=pos_qs)
                .values('drug_name').annotate(qty_sold=Sum('quantity'), revenue=Sum('total'))):
        bucket = med_totals.setdefault(row['drug_name'], {'qty_sold': 0, 'revenue': Decimal('0.00')})
        bucket['qty_sold'] += row['qty_sold'] or 0
        bucket['revenue'] += row['revenue'] or 0
    for row in (rx_items_qs.values('drug_name')
                .annotate(qty_sold=Sum('quantity'), revenue=Sum('_revenue'))):
        bucket = med_totals.setdefault(row['drug_name'], {'qty_sold': 0, 'revenue': Decimal('0.00')})
        bucket['qty_sold'] += row['qty_sold'] or 0
        bucket['revenue'] += row['revenue'] or 0
    medication_summary = [
        {'drug_name': k, 'qty_sold': v['qty_sold'], 'revenue': v['revenue']}
        for k, v in sorted(med_totals.items(), key=lambda kv: -kv[1]['qty_sold'])
    ][:30]

    # Daily trend — merge both streams by calendar day.
    daily_totals = {}
    for row in (pos_qs.annotate(day=TruncDate('created_at'))
                .values('day').annotate(count=Count('id'), revenue=Sum('total_amount'))):
        bucket = daily_totals.setdefault(row['day'], {'count': 0, 'revenue': Decimal('0.00')})
        bucket['count'] += row['count']
        bucket['revenue'] += row['revenue'] or 0
    for row in (rx_items_qs.annotate(day=TruncDate('prescription__created_at'))
                .values('day').annotate(count=Count('prescription_id', distinct=True), revenue=Sum('_revenue'))):
        bucket = daily_totals.setdefault(row['day'], {'count': 0, 'revenue': Decimal('0.00')})
        bucket['count'] += row['count']
        bucket['revenue'] += row['revenue'] or 0
    daily = [
        {'day': day, 'count': v['count'], 'revenue': v['revenue']}
        for day, v in sorted(daily_totals.items(), key=lambda kv: kv[0])
    ]

    # Prescription dispensing rows, grouped one-per-prescription to mirror
    # the walk-in Sales Transactions table below.
    rx_rows = list(
        rx_items_qs.values(
            'prescription_id', 'prescription__prescription_number',
            'prescription__patient__first_name', 'prescription__patient__last_name',
            'prescription__created_at', 'prescription__status',
        )
        .annotate(total=Sum('_revenue'), item_count=Count('id'))
        .order_by('-prescription__created_at')
    )

    if export in ('excel', 'csv'):
        headers = ['Sale #', 'Date', 'Customer', 'Type', 'Status', 'Payment Method',
                   'Subtotal', 'Discount', 'Total', 'Paid']
        rows = []
        for s in pos_qs.values('sale_number', 'created_at', 'customer_name',
                           'patient__first_name', 'patient__last_name',
                           'sale_type', 'status', 'payment_method',
                           'subtotal', 'discount_amount', 'total_amount', 'paid_amount'):
            customer = (f"{s['patient__first_name']} {s['patient__last_name']}"
                        if s['patient__first_name'] else s['customer_name'] or 'Walk-in')
            rows.append([
                s['sale_number'],
                timezone.localtime(s['created_at']).strftime('%d %b %Y %H:%M'),
                customer, s['sale_type'], s['status'], s['payment_method'] or '—',
                s['subtotal'], s['discount_amount'], s['total_amount'], s['paid_amount'],
            ])
        for r in rx_rows:
            customer = f"{r['prescription__patient__first_name']} {r['prescription__patient__last_name']}"
            rows.append([
                r['prescription__prescription_number'],
                timezone.localtime(r['prescription__created_at']).strftime('%d %b %Y %H:%M'),
                customer, 'Prescription', r['prescription__status'], '—',
                r['total'], 0, r['total'], r['total'],
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'pharmacy_sales_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Pharmacy Sales Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'pharmacy_sales_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(pos_qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    rx_paginator = Paginator(rx_rows, 50)
    rx_page_obj  = rx_paginator.get_page(request.GET.get('rx_page', 1))

    return render(request, 'reports/pharmacy_summary.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f,
        'totals': totals,
        'method_summary': method_summary,
        'medication_summary': medication_summary,
        'daily': daily,
        'page_obj': page_obj,
        'rx_page_obj': rx_page_obj,
        'sale_statuses': PharmacySale.Status.choices,
        'qp': _qp(request),
    })


# ── Prescription Report ───────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_reports')
def report_prescriptions(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f = request.GET.get('status', '')
    q        = request.GET.get('q', '').strip()
    export   = request.GET.get('export', '')

    qs = Prescription.objects.select_related(
        'patient', 'visit__department', 'prescribed_by'
    ).filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).order_by('-created_at')

    if status_f:
        qs = qs.filter(status=status_f)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(prescription_number__icontains=q)
        )

    total = qs.count()

    status_summary = qs.values('status').annotate(count=Count('id')).order_by('-count')

    # Top prescribed medications
    top_meds = (
        PrescriptionItem.objects
        .filter(prescription__in=qs)
        .values('drug_name')
        .annotate(count=Count('id'), total_qty=Sum('quantity'))
        .order_by('-count')[:20]
    )

    # Top prescribers
    top_prescribers = (
        qs.values('prescribed_by__first_name', 'prescribed_by__last_name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    if export in ('excel', 'csv'):
        headers = ['Rx #', 'Date', 'Patient', 'MRN', 'Prescribed By',
                   'Department', 'Status', 'Items', 'Diagnosis']
        rows = []
        for rx in qs.values(
            'prescription_number', 'created_at',
            'patient__first_name', 'patient__last_name', 'patient__card_number',
            'prescribed_by__first_name', 'prescribed_by__last_name',
            'visit__department__name', 'status', 'diagnosis',
        ).annotate(item_count=Count('items')):
            rows.append([
                rx['prescription_number'],
                timezone.localtime(rx['created_at']).strftime('%d %b %Y %H:%M'),
                f"{rx['patient__first_name']} {rx['patient__last_name']}",
                rx['patient__card_number'],
                f"Dr. {rx['prescribed_by__first_name']} {rx['prescribed_by__last_name']}",
                rx['visit__department__name'] or '—',
                rx['status'],
                rx['item_count'],
                rx['diagnosis'][:50] if rx['diagnosis'] else '—',
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'prescriptions_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Prescription Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'prescriptions_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'reports/prescriptions.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f, 'q': q,
        'total': total, 'page_obj': page_obj,
        'status_summary': status_summary,
        'top_meds': top_meds,
        'top_prescribers': top_prescribers,
        'rx_statuses': Prescription.Status.choices,
        'qp': _qp(request),
    })


# ── Revenue Report ────────────────────────────────────────────────────────────

@hms_permission_required('core.read_financial_report')
def report_revenue(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    export      = request.GET.get('export', '')

    invoices_qs = Invoice.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    )
    payments_qs = Payment.objects.filter(
        payment_date__gte=from_date,
        payment_date__lte=to_date,
    )

    if dept_filter:
        invoices_qs = invoices_qs.filter(visit__department__id=dept_filter)
        payments_qs = payments_qs.filter(invoice__visit__department__id=dept_filter)

    total_invoiced  = invoices_qs.aggregate(t=Sum('total_amount'))['t'] or 0
    total_collected = payments_qs.aggregate(t=Sum('amount'))['t'] or 0
    total_discount  = invoices_qs.aggregate(t=Sum('discount'))['t'] or 0
    outstanding     = max(float(total_invoiced) - float(total_collected) - float(total_discount), 0)
    collection_rate = round(float(total_collected) / float(total_invoiced) * 100, 1) if total_invoiced else 0

    method_breakdown = (
        payments_qs.values('payment_method')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('-total')
    )
    for m in method_breakdown:
        m['pct'] = round(float(m['total']) / float(total_collected) * 100, 1) if total_collected else 0

    service_breakdown = (
        InvoiceItem.objects.filter(invoice__in=invoices_qs)
        .values('service_type')
        .annotate(total=Sum('total'), count=Count('id'))
        .order_by('-total')
    )
    service_total = sum(float(s['total']) for s in service_breakdown) or 1
    for s in service_breakdown:
        s['pct'] = round(float(s['total']) / service_total * 100, 1)

    dept_revenue = (
        invoices_qs.filter(visit__isnull=False)
        .values('visit__department__name')
        .annotate(invoiced=Sum('total_amount'), paid=Sum('paid_amount'))
        .order_by('-invoiced')[:12]
    )

    daily_rev = (
        payments_qs.values('payment_date')
        .annotate(collected=Sum('amount'))
        .order_by('payment_date')
    )

    if export in ('excel', 'csv'):
        headers = ['Date', 'Payment Method', 'Amount (ETB)', 'Receipt #', 'Cashier', 'Invoice #']
        rows = []
        for p in payments_qs.select_related('invoice', 'received_by').order_by('payment_date'):
            rows.append([
                p.payment_date.strftime('%d %b %Y'),
                p.payment_method,
                float(p.amount),
                p.receipt_number,
                p.received_by.get_full_name() if p.received_by else '—',
                p.invoice.invoice_number if p.invoice else '—',
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'revenue_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Revenue Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'revenue_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/revenue.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'dept_filter': dept_filter,
        'total_invoiced': total_invoiced,
        'total_collected': total_collected,
        'total_discount': total_discount,
        'outstanding': outstanding,
        'collection_rate': collection_rate,
        'method_breakdown': list(method_breakdown),
        'service_breakdown': list(service_breakdown),
        'dept_revenue': list(dept_revenue),
        'daily_rev': list(daily_rev),
        'departments': departments,
        'qp': _qp(request),
    })


# ── Invoice Status Report ─────────────────────────────────────────────────────

@hms_permission_required('core.read_financial_report')
def report_invoice_status(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f    = request.GET.get('status', '')
    dept_filter = request.GET.get('department', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    qs = Invoice.objects.select_related(
        'patient', 'visit__department', 'created_by'
    ).filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).order_by('-created_at')

    if status_f:
        qs = qs.filter(status=status_f)
    if dept_filter:
        qs = qs.filter(visit__department__id=dept_filter)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(invoice_number__icontains=q) | Q(patient__card_number__icontains=q)
        )

    total  = qs.count()
    totals = qs.aggregate(
        invoiced=Sum('total_amount'),
        paid=Sum('paid_amount'),
        discounted=Sum('discount'),
    )

    status_summary = (
        qs.values('status').annotate(count=Count('id'), amount=Sum('total_amount'))
        .order_by('-count')
    )

    if export in ('excel', 'csv'):
        headers = ['Invoice #', 'Date', 'Patient', 'MRN', 'Department',
                   'Status', 'Total (ETB)', 'Paid (ETB)', 'Balance (ETB)']
        rows = []
        for inv in qs.values(
            'invoice_number', 'created_at',
            'patient__first_name', 'patient__last_name', 'patient__card_number',
            'visit__department__name', 'status', 'total_amount', 'paid_amount', 'discount',
        ):
            balance = float(inv['total_amount']) - float(inv['paid_amount']) - float(inv['discount'])
            rows.append([
                inv['invoice_number'],
                timezone.localtime(inv['created_at']).strftime('%d %b %Y %H:%M'),
                f"{inv['patient__first_name']} {inv['patient__last_name']}",
                inv['patient__card_number'],
                inv['visit__department__name'] or '—',
                inv['status'],
                float(inv['total_amount']),
                float(inv['paid_amount']),
                round(balance, 2),
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'invoice_status_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Invoice Status Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'invoice_status_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator   = Paginator(qs, 50)
    page_obj    = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/invoice_status.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f, 'dept_filter': dept_filter, 'q': q,
        'total': total, 'totals': totals,
        'page_obj': page_obj,
        'status_summary': status_summary,
        'invoice_statuses': Invoice.Status.choices,
        'departments': departments,
        'qp': _qp(request),
    })


# ── Employee Report ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_employees')
def report_employees(request):
    dept_filter = request.GET.get('department', '')
    status_f    = request.GET.get('status', '')
    type_f      = request.GET.get('emp_type', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    qs = Employee.objects.select_related('user', 'department').order_by(
        'department__name', 'user__last_name'
    )

    if dept_filter:
        qs = qs.filter(department__id=dept_filter)
    if status_f:
        qs = qs.filter(employment_status=status_f)
    if type_f:
        qs = qs.filter(employment_type=type_f)
    if q:
        qs = qs.filter(
            Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) |
            Q(position__icontains=q)
        )

    total = qs.count()

    dept_summary = (
        qs.values('department__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    status_summary = qs.values('employment_status').annotate(count=Count('id')).order_by('-count')
    type_summary   = qs.values('employment_type').annotate(count=Count('id')).order_by('-count')

    if export in ('excel', 'csv'):
        headers = ['Name', 'Department', 'Position', 'Employment Type', 'Status', 'Hire Date', 'Username']
        rows = [
            [
                emp.full_name,
                emp.department.name if emp.department else '—',
                emp.position or '—',
                emp.employment_type,
                emp.employment_status,
                emp.hire_date.strftime('%d %b %Y') if emp.hire_date else '—',
                emp.user.username,
            ]
            for emp in qs
        ]
        if export == 'excel':
            r = excel_response('employees.xlsx')
            wb = build_workbook('Employee Report', 'Current Records', _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('employees.csv')
        return write_csv(r, headers, rows)

    paginator   = Paginator(qs, 50)
    page_obj    = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/employees.html', {
        'dept_filter': dept_filter, 'status_f': status_f, 'type_f': type_f, 'q': q,
        'total': total, 'page_obj': page_obj,
        'dept_summary': dept_summary,
        'status_summary': status_summary,
        'type_summary': type_summary,
        'departments': departments,
        'emp_statuses': Employee.EmploymentStatus.choices,
        'emp_types': Employee.EmploymentType.choices,
        'qp': _qp(request),
    })


# ── Audit / User Activity Report ──────────────────────────────────────────────

@hms_permission_required('core.read_audit_log')
def report_audit_activity(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    action_f  = request.GET.get('action', '')
    module_f  = request.GET.get('module', '')
    user_f    = request.GET.get('user_id', '')
    severity_f = request.GET.get('severity', '')
    q         = request.GET.get('q', '').strip()
    export    = request.GET.get('export', '')

    qs = AuditLog.objects.filter(
        timestamp__date__gte=from_date,
        timestamp__date__lte=to_date,
    ).select_related('user').order_by('-timestamp')

    if action_f:
        qs = qs.filter(action=action_f)
    if module_f:
        qs = qs.filter(module=module_f)
    if user_f:
        qs = qs.filter(user__id=user_f)
    if severity_f:
        qs = qs.filter(severity=severity_f)
    if q:
        qs = qs.filter(
            Q(description__icontains=q) | Q(user__username__icontains=q) |
            Q(object_repr__icontains=q)
        )

    total = qs.count()

    action_summary = qs.values('action').annotate(count=Count('id')).order_by('-count')
    module_summary = qs.values('module').annotate(count=Count('id')).order_by('-count')
    top_users      = (
        qs.values('user__username', 'user__first_name', 'user__last_name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    if export in ('excel', 'csv'):
        headers = ['Timestamp', 'User', 'Action', 'Module', 'Object', 'Description', 'Severity']
        rows = []
        for log in qs.values('timestamp', 'user__username', 'user__first_name',
                             'user__last_name', 'action', 'module',
                             'object_repr', 'description', 'severity'):
            rows.append([
                timezone.localtime(log['timestamp']).strftime('%d %b %Y %H:%M:%S'),
                log['user__username'] or '—',
                log['action'],
                log['module'],
                log['object_repr'][:60] if log['object_repr'] else '—',
                log['description'][:100] if log['description'] else '—',
                log['severity'],
            ])
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'audit_{from_str}_{to_str}.xlsx')
            wb = build_workbook('User Activity Audit Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'audit_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))
    users     = User.objects.filter(is_active=True).order_by('last_name')

    return render(request, 'reports/audit_activity.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'action_f': action_f, 'module_f': module_f,
        'user_f': user_f, 'severity_f': severity_f, 'q': q,
        'total': total, 'page_obj': page_obj,
        'action_summary': action_summary,
        'module_summary': module_summary,
        'top_users': top_users,
        'users': users,
        'audit_actions': AuditLog.Action.choices,
        'audit_modules': AuditLog.Module.choices,
        'audit_severities': AuditLog.Severity.choices,
        'qp': _qp(request),
    })


# ── Legacy views (kept unchanged for backward compatibility) ──────────────────

@hms_permission_required('core.read_clinical_reports')
def report_patients(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    patients_qs = Patient.objects.filter(
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    total_new = patients_qs.count()
    sex_data = patients_qs.values('sex').annotate(count=Count('id')).order_by('-count')
    for s in sex_data:
        s['pct'] = round(s['count'] / total_new * 100, 1) if total_new else 0
    today = timezone.localdate()
    all_pats_list = list(patients_qs.values('date_of_birth'))
    age_groups = {'0-18': 0, '19-40': 0, '41-60': 0, '60+': 0, 'Unknown': 0}
    for p in all_pats_list:
        dob = p['date_of_birth']
        if not dob:
            age_groups['Unknown'] += 1; continue
        age = (today - dob).days // 365
        if age <= 18: age_groups['0-18'] += 1
        elif age <= 40: age_groups['19-40'] += 1
        elif age <= 60: age_groups['41-60'] += 1
        else: age_groups['60+'] += 1
    age_data = [{'label': k, 'count': v,
                 'pct': round(v / total_new * 100, 1) if total_new else 0}
                for k, v in age_groups.items()]
    region_data = (
        patients_qs.exclude(region='').values('region')
        .annotate(count=Count('id')).order_by('-count')[:10]
    )
    max_region = region_data[0]['count'] if region_data else 1
    for r in region_data:
        r['pct'] = round(r['count'] / max_region * 100) if max_region else 0
    weekly_data = (
        patients_qs.annotate(week=TruncWeek('created_at'))
        .values('week').annotate(count=Count('id')).order_by('week')
    )
    return render(request, 'reports/patients.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'total_new': total_new, 'sex_data': sex_data, 'age_data': age_data,
        'region_data': region_data, 'weekly_data': weekly_data,
    })


@hms_permission_required('core.read_clinical_reports')
def report_clinical(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    visits_qs = Visit.objects.filter(
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    visit_type_summary = (
        visits_qs.values('visit_type').annotate(count=Count('id')).order_by('-count')
    )
    total_visits = visits_qs.count()
    dept_visits = (
        visits_qs.values('department__name')
        .annotate(visit_count=Count('id'),
                  lab_count=Count('lab_orders', distinct=True),
                  imaging_count=Count('imaging_orders', distinct=True))
        .order_by('-visit_count')
    )
    lab_qs = LabOrder.objects.filter(
        ordered_at__date__gte=from_date, ordered_at__date__lte=to_date,
    )
    top_labs = (
        lab_qs.values('test_name')
        .annotate(count=Count('id'),
                  pending=Count('id', filter=Q(status='Pending')),
                  completed=Count('id', filter=Q(status='Completed')))
        .order_by('-count')[:10]
    )
    imaging_qs = ImagingOrder.objects.filter(
        ordered_at__date__gte=from_date, ordered_at__date__lte=to_date,
    )
    top_imaging = (
        imaging_qs.values('imaging_type').annotate(count=Count('id')).order_by('-count')[:10]
    )
    meds_qs = MedicationOrder.objects.filter(
        ordered_at__date__gte=from_date, ordered_at__date__lte=to_date,
    )
    top_meds = (
        meds_qs.values('drug_name').annotate(count=Count('id')).order_by('-count')[:10]
    )
    proc_qs = ProcedureOrder.objects.filter(
        ordered_at__date__gte=from_date, ordered_at__date__lte=to_date,
    )
    proc_by_type = proc_qs.values('procedure_type').annotate(count=Count('id')).order_by('-count')
    return render(request, 'reports/clinical.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'visit_type_summary': visit_type_summary, 'total_visits': total_visits,
        'dept_visits': dept_visits, 'top_labs': top_labs,
        'top_imaging': top_imaging, 'top_meds': top_meds, 'proc_by_type': proc_by_type,
    })


@hms_permission_required('core.read_financial_report')
def report_financial(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    invoices_qs = Invoice.objects.filter(
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    payments_qs = Payment.objects.filter(
        payment_date__gte=from_date, payment_date__lte=to_date,
    )
    total_invoiced  = invoices_qs.aggregate(total=Sum('total_amount'))['total'] or 0
    total_collected = payments_qs.aggregate(total=Sum('amount'))['total'] or 0
    total_discounts = invoices_qs.aggregate(total=Sum('discount'))['total'] or 0
    total_outstanding = max(float(total_invoiced) - float(total_collected) - float(total_discounts), 0)
    collection_rate = round(float(total_collected) / float(total_invoiced) * 100, 1) if total_invoiced else 0
    method_breakdown = (
        payments_qs.values('payment_method')
        .annotate(total=Sum('amount'), count=Count('id')).order_by('-total')
    )
    for m in method_breakdown:
        m['pct'] = round(float(m['total']) / float(total_collected) * 100, 1) if total_collected else 0
    daily_invoiced = (
        invoices_qs.annotate(date=TruncDate('created_at'))
        .values('date').annotate(invoiced=Sum('total_amount')).order_by('date')
    )
    daily_collected = (
        payments_qs.values('payment_date').annotate(collected=Sum('amount')).order_by('payment_date')
    )
    daily_map = {}
    for row in daily_invoiced:
        daily_map[row['date']] = {'date': row['date'], 'invoiced': row['invoiced'], 'collected': 0}
    for row in daily_collected:
        d = row['payment_date']
        if d in daily_map:
            daily_map[d]['collected'] = row['collected']
        else:
            daily_map[d] = {'date': d, 'invoiced': 0, 'collected': row['collected']}
    daily_revenue = sorted(daily_map.values(), key=lambda x: x['date'])
    top_services = (
        InvoiceItem.objects.filter(invoice__in=invoices_qs)
        .values('service_type')
        .annotate(total=Sum('total'), count=Count('id')).order_by('-total')
    )
    return render(request, 'reports/financial.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'total_invoiced': total_invoiced, 'total_collected': total_collected,
        'total_discounts': total_discounts, 'total_outstanding': total_outstanding,
        'collection_rate': collection_rate, 'method_breakdown': method_breakdown,
        'daily_revenue': daily_revenue, 'top_services': top_services,
    })


@hms_permission_required('core.read_department_reports')
def report_staff(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    top_doctors = (
        Doctor.objects
        .filter(visits__created_at__date__gte=from_date, visits__created_at__date__lte=to_date)
        .annotate(visit_count=Count('visits', distinct=True),
                  notes_count=Count('visits__clinical_notes', distinct=True))
        .select_related('department')
        .order_by('-visit_count')[:15]
    )
    lab_staff = (
        LabOrder.objects
        .filter(resulted_at__date__gte=from_date, resulted_at__date__lte=to_date, status='Completed')
        .values('ordered_by__first_name', 'ordered_by__last_name', 'ordered_by__username')
        .annotate(results_entered=Count('id')).order_by('-results_entered')[:10]
    )
    imaging_staff = (
        ImagingOrder.objects
        .filter(ordered_at__date__gte=from_date, ordered_at__date__lte=to_date, status='Completed')
        .values('ordered_by__first_name', 'ordered_by__last_name', 'ordered_by__username')
        .annotate(count=Count('id')).order_by('-count')[:10]
    )
    notes_by_author = (
        ClinicalNote.objects
        .filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
        .values('authored_by__first_name', 'authored_by__last_name',
                'authored_by__username', 'note_type')
        .annotate(count=Count('id')).order_by('-count')[:15]
    )
    return render(request, 'reports/staff.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'top_doctors': top_doctors, 'lab_staff': lab_staff,
        'imaging_staff': imaging_staff, 'notes_by_author': notes_by_author,
    })


# ══════════════════════════════════════════════════════════════════════════════
# FACILITY MANAGEMENT REPORTS
# ══════════════════════════════════════════════════════════════════════════════
# Occupancy/status reports (bed, ward, room, available-bed, room-status,
# maintenance) are current-state snapshots — there's no historical
# occupancy time-series table, so a date range wouldn't mean anything for
# them. Event-log reports (admission/discharge, transfer, OR utilization)
# do support a real date range since they're backed by timestamped rows
# (Admission, SurgerySchedule).

@hms_permission_required('core.view_facility_reports')
def report_bed_occupancy(request):
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    qs = Bed.objects.select_related('room', 'ward', 'ward__department', 'current_patient')
    if dept_filter:
        qs = qs.filter(ward__department_id=dept_filter)

    total = qs.count()
    occupied = qs.filter(status=Bed.Status.OCCUPIED).count()
    available = qs.filter(status=Bed.Status.AVAILABLE).count()
    occupancy_rate = round((occupied / total) * 100, 1) if total else 0

    if export in ('excel', 'csv'):
        headers = ['Bed Code', 'Ward', 'Room', 'Status', 'Current Patient']
        rows = [[b.bed_code, b.ward.name, b.room.room_number, b.get_status_display(),
                 b.current_patient.full_name if b.current_patient else '—'] for b in qs]
        if export == 'excel':
            r = excel_response('bed_occupancy.xlsx')
            wb = build_workbook('Bed Occupancy Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('bed_occupancy.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_bed_occupancy.html', {
        'beds': qs.order_by('ward__name', 'room__room_number', 'bed_number'),
        'total': total, 'occupied': occupied, 'available': available, 'occupancy_rate': occupancy_rate,
        'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_ward_occupancy(request):
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    qs = Ward.objects.select_related('department').annotate(
        n_beds=Count('beds', distinct=True),
        n_occupied=Count('beds', filter=Q(beds__status=Bed.Status.OCCUPIED), distinct=True),
    )
    if dept_filter:
        qs = qs.filter(department_id=dept_filter)

    rows_data = [
        (w, w.n_occupied, w.n_beds, round((w.n_occupied / w.n_beds) * 100, 1) if w.n_beds else 0)
        for w in qs.order_by('name')
    ]

    if export in ('excel', 'csv'):
        headers = ['Ward', 'Code', 'Type', 'Status', 'Occupied', 'Total Beds', 'Occupancy %']
        rows = [[w.name, w.code, w.get_ward_type_display(), w.get_status_display(), occ, total_b, rate] for w, occ, total_b, rate in rows_data]
        if export == 'excel':
            r = excel_response('ward_occupancy.xlsx')
            wb = build_workbook('Ward Occupancy Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('ward_occupancy.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_ward_occupancy.html', {
        'rows_data': rows_data,
        'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_room_utilization(request):
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    qs = Room.objects.select_related('ward', 'ward__department').annotate(
        n_beds=Count('beds', distinct=True),
        n_occupied=Count('beds', filter=Q(beds__status=Bed.Status.OCCUPIED), distinct=True),
    )
    if dept_filter:
        qs = qs.filter(ward__department_id=dept_filter)
    qs = qs.order_by('ward__name', 'room_number')

    if export in ('excel', 'csv'):
        headers = ['Ward', 'Room', 'Type', 'Status', 'Beds', 'Occupied']
        rows = [[r.ward.name, r.room_number, r.get_room_type_display(), r.get_status_display(), r.n_beds, r.n_occupied] for r in qs]
        if export == 'excel':
            r = excel_response('room_utilization.xlsx')
            wb = build_workbook('Room Utilization Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('room_utilization.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_room_utilization.html', {
        'rooms': qs, 'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_or_utilization(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    rooms = ORRoom.objects.select_related('department')
    if dept_filter:
        rooms = rooms.filter(department_id=dept_filter)

    rows_data = []
    for room in rooms.order_by('name'):
        surgeries = SurgeryOrder.objects.filter(
            schedule__or_room=room, schedule__scheduled_date__gte=from_date, schedule__scheduled_date__lte=to_date,
        ).count()
        days = (to_date - from_date).days + 1
        capacity = (room.max_surgeries_per_day or 0) * days
        util_rate = round((surgeries / capacity) * 100, 1) if capacity else 0
        rows_data.append((room, surgeries, capacity, util_rate))

    if export in ('excel', 'csv'):
        headers = ['OR Room', 'Code', 'Specialty', 'Surgeries Performed', 'Capacity (period)', 'Utilization %']
        rows = [[room.name, room.code, room.get_room_type_display(), surgeries, capacity, util_rate] for room, surgeries, capacity, util_rate in rows_data]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response('or_utilization.xlsx')
            wb = build_workbook('Operating Room Utilization Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('or_utilization.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_or_utilization.html', {
        'from_date': from_date, 'to_date': to_date, 'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'rows_data': rows_data, 'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_admission_discharge(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    qs = Admission.objects.select_related('patient', 'bed__room__ward__department', 'admitted_by', 'discharged_by').filter(
        admitted_at__date__gte=from_date, admitted_at__date__lte=to_date,
    )
    if dept_filter:
        qs = qs.filter(bed__room__ward__department_id=dept_filter)
    qs = qs.order_by('-admitted_at')

    if export in ('excel', 'csv'):
        headers = ['Patient', 'MRN', 'Ward', 'Bed', 'Status', 'Admitted At', 'Admitted By', 'Discharged At']
        rows = [[
            a.patient.full_name, a.patient.card_number, a.bed.room.ward.name, a.bed.bed_code, a.get_status_display(),
            timezone.localtime(a.admitted_at).strftime('%d %b %Y %H:%M'),
            a.admitted_by.get_full_name() or a.admitted_by.username,
            timezone.localtime(a.discharged_at).strftime('%d %b %Y %H:%M') if a.discharged_at else '—',
        ] for a in qs]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response('admission_discharge.xlsx')
            wb = build_workbook('Admission & Discharge Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('admission_discharge.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_admission_discharge.html', {
        'from_date': from_date, 'to_date': to_date, 'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'admissions': qs, 'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_patient_transfer(request):
    from_date, to_date, from_date_str, to_date_str = _parse_date_range(request)
    export = request.GET.get('export', '')

    qs = Admission.objects.select_related('patient', 'bed__room__ward', 'discharged_by').filter(
        status=Admission.Status.TRANSFERRED, discharged_at__date__gte=from_date, discharged_at__date__lte=to_date,
    ).order_by('-discharged_at')

    if export in ('excel', 'csv'):
        headers = ['Patient', 'MRN', 'From Bed', 'From Ward', 'Transferred At', 'Transferred By', 'Reason']
        rows = [[
            a.patient.full_name, a.patient.card_number, a.bed.bed_code, a.bed.room.ward.name,
            timezone.localtime(a.discharged_at).strftime('%d %b %Y %H:%M') if a.discharged_at else '—',
            a.discharged_by.get_full_name() if a.discharged_by else '—', a.transfer_reason or '—',
        ] for a in qs]
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response('patient_transfer.xlsx')
            wb = build_workbook('Patient Transfer Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('patient_transfer.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_patient_transfer.html', {
        'from_date': from_date, 'to_date': to_date, 'from_date_str': from_date_str, 'to_date_str': to_date_str,
        'transfers': qs, 'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_facility_maintenance(request):
    export = request.GET.get('export', '')

    beds = Bed.objects.select_related('room', 'ward').filter(status__in=[Bed.Status.MAINTENANCE, Bed.Status.OUT_OF_SERVICE])
    rooms = Room.objects.select_related('ward').filter(status=Room.Status.MAINTENANCE)
    wards = Ward.objects.filter(status=Ward.Status.MAINTENANCE)
    or_rooms = ORRoom.objects.filter(availability_status=ORRoom.Availability.MAINTENANCE)

    if export in ('excel', 'csv'):
        headers = ['Type', 'Name', 'Location', 'Status']
        rows = []
        rows += [['Bed', b.bed_code, f'{b.ward.name} / {b.room.room_number}', b.get_status_display()] for b in beds]
        rows += [['Room', r.room_number, r.ward.name, r.get_status_display()] for r in rooms]
        rows += [['Ward', w.name, w.code, w.get_status_display()] for w in wards]
        rows += [['OR Room', o.name, o.code or '—', o.get_availability_status_display()] for o in or_rooms]
        if export == 'excel':
            r = excel_response('facility_maintenance.xlsx')
            wb = build_workbook('Facility Maintenance Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('facility_maintenance.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_maintenance.html', {
        'beds': beds, 'rooms': rooms, 'wards': wards, 'or_rooms': or_rooms, 'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_available_beds(request):
    dept_filter = request.GET.get('department', '')
    ward_type_filter = request.GET.get('ward_type', '')
    export = request.GET.get('export', '')

    qs = Bed.objects.select_related('room', 'ward', 'ward__department').filter(
        status=Bed.Status.AVAILABLE, room__status=Room.Status.ACTIVE, ward__status=Ward.Status.ACTIVE,
    )
    if dept_filter:
        qs = qs.filter(ward__department_id=dept_filter)
    if ward_type_filter:
        qs = qs.filter(ward__ward_type=ward_type_filter)
    qs = qs.order_by('ward__name', 'room__room_number', 'bed_number')

    if export in ('excel', 'csv'):
        headers = ['Bed Code', 'Ward', 'Ward Type', 'Room', 'Room Type', 'Gender Restriction']
        rows = [[b.bed_code, b.ward.name, b.ward.get_ward_type_display(), b.room.room_number,
                 b.room.get_room_type_display(), b.ward.get_gender_restriction_display()] for b in qs]
        if export == 'excel':
            r = excel_response('available_beds.xlsx')
            wb = build_workbook('Available Bed Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('available_beds.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_available_beds.html', {
        'beds': qs, 'dept_filter': dept_filter, 'ward_type_filter': ward_type_filter,
        'departments': Department.objects.filter(is_active=True), 'ward_types': Ward.WardType.choices,
        'qp': _qp(request),
    })


@hms_permission_required('core.view_facility_reports')
def report_room_status(request):
    dept_filter = request.GET.get('department', '')
    export = request.GET.get('export', '')

    qs = Room.objects.select_related('ward', 'ward__department').annotate(n_beds=Count('beds'))
    if dept_filter:
        qs = qs.filter(ward__department_id=dept_filter)
    qs = qs.order_by('ward__name', 'room_number')

    if export in ('excel', 'csv'):
        headers = ['Ward', 'Room', 'Type', 'Privacy', 'Beds', 'AC', 'Isolation', 'Status']
        rows = [[r.ward.name, r.room_number, r.get_room_type_display(), r.get_privacy_type_display(),
                 r.n_beds, 'Yes' if r.is_ac else 'No', 'Yes' if r.is_isolation else 'No', r.get_status_display()] for r in qs]
        if export == 'excel':
            r = excel_response('room_status.xlsx')
            wb = build_workbook('Room Status Report', timezone.localdate().strftime('%d %b %Y'), _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response('room_status.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/facility_room_status.html', {
        'rooms': qs, 'dept_filter': dept_filter, 'departments': Department.objects.filter(is_active=True),
        'qp': _qp(request),
    })


# ─────────────────────────────────────────────────────────────────────────────
# CARD & CONSULTATION TYPE REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _render_card_report(request, title, headers, rows, filename_prefix, *,
                         from_date=None, to_date=None, from_str=None, to_str=None,
                         show_date_filter=True, departments=None, dept_filter='',
                         card_types=None, card_type_filter='',
                         consultation_types=None, consultation_type_filter='',
                         specializations=None, specialization_filter='',
                         categories=None, category_filter='',
                         store_locations=None, store_location_filter='',
                         domains=None, domain_filter='',
                         periods=None, period_filter=''):
    export = request.GET.get('export', '')
    if export in ('excel', 'csv'):
        period = _period_label(from_date, to_date) if from_date and to_date else 'All time'
        if export == 'excel':
            r = excel_response(f'{filename_prefix}.xlsx')
            wb = build_workbook(title, period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'{filename_prefix}.csv')
        return write_csv(r, headers, rows)

    return render(request, 'reports/card_generic_report.html', {
        'title': title,
        'headers': headers,
        'rows': rows,
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'show_date_filter': show_date_filter,
        'departments': departments, 'dept_filter': dept_filter,
        'card_types': card_types, 'card_type_filter': card_type_filter,
        'consultation_types': consultation_types, 'consultation_type_filter': consultation_type_filter,
        'specializations': specializations, 'specialization_filter': specialization_filter,
        'categories': categories, 'category_filter': category_filter,
        'store_locations': store_locations, 'store_location_filter': store_location_filter,
        'domains': domains, 'domain_filter': domain_filter,
        'periods': periods, 'period_filter': period_filter,
        'qp': _qp(request),
    })


@hms_permission_required('core.view_card_reports')
def report_card_type_usage(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    card_type_filter = request.GET.get('card_type', '')

    period_qs = PatientCard.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    card_types_qs = CardType.objects.all().order_by('name')
    if card_type_filter:
        card_types_qs = card_types_qs.filter(pk=card_type_filter)

    rows = []
    for ct in card_types_qs:
        ct_period = period_qs.filter(card_type=ct)
        issued = ct_period.filter(renewed_from__isnull=True).count()
        renewed = ct_period.filter(renewed_from__isnull=False).count()
        active_now = PatientCard.objects.filter(card_type=ct, status=PatientCard.Status.ACTIVE).count()
        expired_now = sum(
            1 for c in PatientCard.objects.filter(card_type=ct).exclude(
                status__in=[PatientCard.Status.RENEWED, PatientCard.Status.CANCELLED],
            ) if c.is_expired
        )
        rows.append([ct.name, issued, renewed, ct_period.count(), active_now, expired_now])

    headers = ['Card Type', 'Newly Issued', 'Renewed', 'Total in Period', 'Currently Active', 'Currently Expired']
    return _render_card_report(
        request, 'Card Type Usage Report', headers, rows, f'card_type_usage_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        card_types=CardType.objects.all().order_by('name'), card_type_filter=card_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_card_renewal(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    card_type_filter = request.GET.get('card_type', '')

    qs = (
        PatientCard.objects
        .filter(renewed_from__isnull=False, created_at__date__gte=from_date, created_at__date__lte=to_date)
        .select_related('patient', 'card_type', 'issued_by', 'renewed_from')
        .order_by('-created_at')
    )
    if card_type_filter:
        qs = qs.filter(card_type_id=card_type_filter)

    rows = [[
        c.patient.full_name, c.patient.card_number, c.card_type.name,
        c.renewed_from.expiry_date.strftime('%d %b %Y') if c.renewed_from and c.renewed_from.expiry_date else '—',
        c.issued_date.strftime('%d %b %Y'),
        c.expiry_date.strftime('%d %b %Y') if c.expiry_date else 'Lifetime',
        f'ETB {c.fee_paid:,.2f}' + (' (waived)' if c.fee_waived else ''),
        c.issued_by.get_full_name() or c.issued_by.username,
    ] for c in qs]

    headers = ['Patient', 'MRN', 'Card Type', 'Previous Expiry', 'Renewed On', 'New Expiry', 'Fee', 'Renewed By']
    return _render_card_report(
        request, 'Card Renewal Report', headers, rows, f'card_renewal_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        card_types=CardType.objects.all().order_by('name'), card_type_filter=card_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_expired_cards(request):
    card_type_filter = request.GET.get('card_type', '')
    today = timezone.localdate()

    qs = (
        PatientCard.objects
        .filter(expiry_date__lt=today)
        .exclude(status__in=[PatientCard.Status.RENEWED, PatientCard.Status.CANCELLED])
        .select_related('patient', 'card_type')
        .order_by('expiry_date')
    )
    if card_type_filter:
        qs = qs.filter(card_type_id=card_type_filter)

    rows = [[
        c.patient.full_name, c.patient.card_number, c.card_type.name,
        c.expiry_date.strftime('%d %b %Y'), (today - c.expiry_date).days,
        c.patient.mobile or '—',
    ] for c in qs]

    headers = ['Patient', 'MRN', 'Card Type', 'Expired On', 'Days Expired', 'Phone']
    return _render_card_report(
        request, 'Expired Card Report', headers, rows, f'expired_cards_{today}',
        show_date_filter=False,
        card_types=CardType.objects.all().order_by('name'), card_type_filter=card_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_consultation_type(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    consultation_type_filter = request.GET.get('consultation_type', '')

    qs = (
        Visit.objects
        .filter(consultation_type__isnull=False, created_at__date__gte=from_date, created_at__date__lte=to_date)
    )
    if dept_filter:
        qs = qs.filter(department_id=dept_filter)
    if consultation_type_filter:
        qs = qs.filter(consultation_type_id=consultation_type_filter)

    grouped = (
        qs.values('consultation_type__name', 'consultation_type__department__name', 'consultation_type__fee')
        .annotate(visit_count=Count('id'))
        .order_by('consultation_type__department__name', 'consultation_type__name')
    )
    rows = [[
        g['consultation_type__name'], g['consultation_type__department__name'] or 'Hospital-wide',
        g['visit_count'], f"ETB {g['consultation_type__fee']:,.2f}",
        f"ETB {(g['consultation_type__fee'] * g['visit_count']):,.2f}",
    ] for g in grouped]

    headers = ['Consultation Type', 'Department', 'Visit Count', 'Fee', 'Est. Revenue']
    return _render_card_report(
        request, 'Consultation Type Report', headers, rows, f'consultation_type_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        departments=Department.objects.filter(is_active=True).order_by('name'), dept_filter=dept_filter,
        consultation_types=ConsultationType.objects.select_related('department').order_by('department__name', 'name'),
        consultation_type_filter=consultation_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_revenue_by_card_type(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    card_type_filter = request.GET.get('card_type', '')

    qs = (
        InvoiceItem.objects
        .filter(
            service_type=InvoiceItem.ServiceType.CARD_FEE,
            patient_card__isnull=False,
            patient_card__issued_date__gte=from_date,
            patient_card__issued_date__lte=to_date,
        )
    )
    if card_type_filter:
        qs = qs.filter(patient_card__card_type_id=card_type_filter)

    grouped = (
        qs.values('patient_card__card_type__name')
        .annotate(charged=Sum('total'), collected=Sum('paid_amount'), count=Count('id'))
        .order_by('-collected')
    )
    rows = [[
        g['patient_card__card_type__name'], g['count'],
        f"ETB {g['charged'] or 0:,.2f}", f"ETB {g['collected'] or 0:,.2f}",
    ] for g in grouped]

    headers = ['Card Type', 'Cards Charged', 'Total Charged', 'Total Collected']
    return _render_card_report(
        request, 'Revenue by Card Type', headers, rows, f'revenue_card_type_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        card_types=CardType.objects.all().order_by('name'), card_type_filter=card_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_revenue_by_consultation_type(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    consultation_type_filter = request.GET.get('consultation_type', '')

    qs = (
        InvoiceItem.objects
        .filter(
            service_type=InvoiceItem.ServiceType.CONSULTATION,
            invoice__visit__consultation_type__isnull=False,
            invoice__visit__created_at__date__gte=from_date,
            invoice__visit__created_at__date__lte=to_date,
        )
    )
    if consultation_type_filter:
        qs = qs.filter(invoice__visit__consultation_type_id=consultation_type_filter)

    grouped = (
        qs.values('invoice__visit__consultation_type__name', 'invoice__visit__consultation_type__department__name')
        .annotate(charged=Sum('total'), collected=Sum('paid_amount'), count=Count('id'))
        .order_by('-collected')
    )
    rows = [[
        g['invoice__visit__consultation_type__name'],
        g['invoice__visit__consultation_type__department__name'] or 'Hospital-wide',
        g['count'], f"ETB {g['charged'] or 0:,.2f}", f"ETB {g['collected'] or 0:,.2f}",
    ] for g in grouped]

    headers = ['Consultation Type', 'Department', 'Count', 'Total Charged', 'Total Collected']
    return _render_card_report(
        request, 'Revenue by Consultation Type', headers, rows, f'revenue_consultation_type_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        consultation_types=ConsultationType.objects.select_related('department').order_by('department__name', 'name'),
        consultation_type_filter=consultation_type_filter,
    )


@hms_permission_required('core.view_card_reports')
def report_department_consultation_stats(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')

    qs = Visit.objects.filter(
        consultation_type__isnull=False,
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    if dept_filter:
        qs = qs.filter(department_id=dept_filter)

    grouped = (
        qs.values('department__name')
        .annotate(
            visit_count=Count('id'),
            distinct_types=Count('consultation_type', distinct=True),
        )
        .order_by('-visit_count')
    )
    rows = [[g['department__name'], g['visit_count'], g['distinct_types']] for g in grouped]

    headers = ['Department', 'Consultations', 'Consultation Types Used']
    return _render_card_report(
        request, 'Department-wise Consultation Statistics', headers, rows,
        f'dept_consultation_stats_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        departments=Department.objects.filter(is_active=True).order_by('name'), dept_filter=dept_filter,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DOCTOR SPECIALIZATION REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_specialization_reports')
def report_doctors_by_department(request):
    dept_filter = request.GET.get('department', '')
    qs = Doctor.objects.filter(active=True)
    if dept_filter:
        qs = qs.filter(department_id=dept_filter)

    grouped = qs.values('department__name').annotate(count=Count('id')).order_by('-count')
    rows = [[g['department__name'] or '—', g['count']] for g in grouped]

    headers = ['Department', 'Doctors']
    return _render_card_report(
        request, 'Doctors by Department', headers, rows, 'doctors_by_department',
        show_date_filter=False,
        departments=Department.objects.filter(is_active=True).order_by('name'), dept_filter=dept_filter,
    )


@hms_permission_required('core.view_specialization_reports')
def report_doctors_by_specialization(request):
    specialization_filter = request.GET.get('specialization', '')
    qs = Doctor.objects.filter(active=True, specialization__isnull=False)
    if specialization_filter:
        qs = qs.filter(specialization_id=specialization_filter)

    grouped = (
        qs.values('specialization__name', 'department__name')
        .annotate(count=Count('id'))
        .order_by('specialization__name')
    )
    rows = [[g['specialization__name'], g['department__name'] or '—', g['count']] for g in grouped]

    headers = ['Specialization', 'Department', 'Doctors']
    return _render_card_report(
        request, 'Doctors by Specialization', headers, rows, 'doctors_by_specialization',
        show_date_filter=False,
        specializations=Specialization.objects.all().order_by('name'), specialization_filter=specialization_filter,
    )


@hms_permission_required('core.view_specialization_reports')
def report_patient_volume_by_specialization(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    specialization_filter = request.GET.get('specialization', '')

    qs = Visit.objects.filter(
        doctor__specialization__isnull=False,
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    if specialization_filter:
        qs = qs.filter(doctor__specialization_id=specialization_filter)

    grouped = (
        qs.values('doctor__specialization__name')
        .annotate(visit_count=Count('id'), patient_count=Count('patient', distinct=True))
        .order_by('-visit_count')
    )
    rows = [[g['doctor__specialization__name'], g['patient_count'], g['visit_count']] for g in grouped]

    headers = ['Specialization', 'Unique Patients', 'Total Visits']
    return _render_card_report(
        request, 'Patient Volume by Specialization', headers, rows, f'patient_volume_specialization_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        specializations=Specialization.objects.all().order_by('name'), specialization_filter=specialization_filter,
    )


@hms_permission_required('core.view_specialization_reports')
def report_revenue_by_specialization(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    specialization_filter = request.GET.get('specialization', '')

    qs = InvoiceItem.objects.filter(
        service_type=InvoiceItem.ServiceType.CONSULTATION,
        invoice__visit__doctor__specialization__isnull=False,
        invoice__visit__created_at__date__gte=from_date,
        invoice__visit__created_at__date__lte=to_date,
    )
    if specialization_filter:
        qs = qs.filter(invoice__visit__doctor__specialization_id=specialization_filter)

    grouped = (
        qs.values('invoice__visit__doctor__specialization__name')
        .annotate(charged=Sum('total'), collected=Sum('paid_amount'), count=Count('id'))
        .order_by('-collected')
    )
    rows = [[
        g['invoice__visit__doctor__specialization__name'], g['count'],
        f"ETB {g['charged'] or 0:,.2f}", f"ETB {g['collected'] or 0:,.2f}",
    ] for g in grouped]

    headers = ['Specialization', 'Consultations', 'Total Charged', 'Total Collected']
    return _render_card_report(
        request, 'Revenue by Specialization', headers, rows, f'revenue_specialization_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        specializations=Specialization.objects.all().order_by('name'), specialization_filter=specialization_filter,
    )


@hms_permission_required('core.view_specialization_reports')
def report_appointment_stats_by_specialization(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    specialization_filter = request.GET.get('specialization', '')

    qs = Appointment.objects.filter(
        doctor__specialization__isnull=False,
        appointment_date__gte=from_date, appointment_date__lte=to_date,
    )
    if specialization_filter:
        qs = qs.filter(doctor__specialization_id=specialization_filter)

    grouped = (
        qs.values('doctor__specialization__name')
        .annotate(
            total=Count('id'),
            completed=Count('id', filter=Q(status=Appointment.Status.COMPLETED)),
            cancelled=Count('id', filter=Q(status=Appointment.Status.CANCELLED)),
            no_show=Count('id', filter=Q(status=Appointment.Status.NO_SHOW)),
        )
        .order_by('-total')
    )
    rows = [[
        g['doctor__specialization__name'], g['total'], g['completed'], g['cancelled'], g['no_show'],
    ] for g in grouped]

    headers = ['Specialization', 'Total Appointments', 'Completed', 'Cancelled', 'No Show']
    return _render_card_report(
        request, 'Appointment Statistics by Specialization', headers, rows,
        f'appointment_stats_specialization_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        specializations=Specialization.objects.all().order_by('name'), specialization_filter=specialization_filter,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL INVENTORY COUNT & INVENTORY PERIOD REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _pic_common_filters(request):
    domain_filter = request.GET.get('domain', '')
    category_filter = request.GET.get('category', '')
    store_location_filter = request.GET.get('store_location', '')
    period_filter = request.GET.get('period', '')
    return domain_filter, category_filter, store_location_filter, period_filter


def _pic_filter_kwargs(domain_filter='', category_filter='', store_location_filter='', period_filter=''):
    return dict(
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
        categories=InventoryCategory.objects.order_by('name'), category_filter=category_filter,
        store_locations=DepartmentStore.objects.filter(is_active=True).order_by('name'), store_location_filter=store_location_filter,
        periods=InventoryPeriod.objects.order_by('-start_date'), period_filter=period_filter,
    )


@hms_permission_required('core.view_store_reports')
def report_physical_count(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    domain_filter, category_filter, store_location_filter, period_filter = _pic_common_filters(request)

    qs = PhysicalCountLine.objects.select_related('physical_count', 'physical_count__department_store').filter(
        physical_count__created_at__date__gte=from_date, physical_count__created_at__date__lte=to_date,
    )
    if domain_filter:
        qs = qs.filter(physical_count__domain=domain_filter)
    if category_filter:
        qs = qs.filter(category_name=InventoryCategory.objects.filter(pk=category_filter).values_list('name', flat=True).first())
    if store_location_filter:
        qs = qs.filter(physical_count__department_store_id=store_location_filter)
    if period_filter:
        qs = qs.filter(physical_count__period_id=period_filter)

    rows = [[
        l.physical_count.count_number, l.item_name, l.item_code, l.category_name or '—',
        l.system_quantity, l.physical_quantity if l.physical_quantity is not None else '—',
        l.variance if l.variance is not None else '—',
        f"ETB {l.adjustment_value:,.2f}" if l.adjustment_value else '—',
        l.counted_by.get_full_name() if l.counted_by else '—',
        l.verified_by.get_full_name() if l.verified_by else '—',
    ] for l in qs.order_by('physical_count__count_number', 'item_name')]

    headers = ['Count #', 'Item', 'Code', 'Category', 'System Qty', 'Physical Qty', 'Variance', 'Adjustment Value', 'Counted By', 'Verified By']
    return _render_card_report(
        request, 'Physical Inventory Count Report', headers, rows, f'physical_count_report_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        **_pic_filter_kwargs(domain_filter, category_filter, store_location_filter, period_filter),
    )


@hms_permission_required('core.view_store_reports')
def report_inventory_reconciliation(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    domain_filter, _, _, period_filter = _pic_common_filters(request)

    qs = PhysicalCount.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    if domain_filter:
        qs = qs.filter(domain=domain_filter)
    if period_filter:
        qs = qs.filter(period_id=period_filter)

    rows = []
    for c in qs.select_related('period', 'created_by', 'approved_by').order_by('-created_at'):
        lines = list(c.lines.all())
        variance_lines = [l for l in lines if l.has_variance]
        total_adj = sum((l.adjustment_value or Decimal('0.00') for l in variance_lines), Decimal('0.00'))
        rows.append([
            c.count_number, c.get_count_type_display(), c.get_domain_display(), c.status,
            len(lines), len(variance_lines), f"ETB {total_adj:,.2f}",
            c.created_by.get_full_name() if c.created_by else '—',
            c.approved_by.get_full_name() if c.approved_by else '—',
        ])

    headers = ['Count #', 'Type', 'Domain', 'Status', 'Items Counted', 'Items with Variance', 'Net Adjustment Value', 'Started By', 'Approved By']
    return _render_card_report(
        request, 'Inventory Reconciliation Report', headers, rows, f'inventory_reconciliation_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
        periods=InventoryPeriod.objects.order_by('-start_date'), period_filter=period_filter,
    )


@hms_permission_required('core.view_store_reports')
def report_inventory_adjustments(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    domain_filter, _, _, _ = _pic_common_filters(request)

    qs = InventoryAdjustment.objects.select_related('approved_by', 'physical_count_line__physical_count').filter(
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    if domain_filter:
        qs = qs.filter(domain=domain_filter)

    rows = [[
        a.physical_count_line.physical_count.count_number, a.item_description,
        dict(PhysicalCount.Domain.choices).get(a.domain, a.domain),
        a.previous_quantity, a.physical_quantity, a.difference, a.adjustment_type,
        f"ETB {a.adjustment_value:,.2f}", a.reason or '—',
        a.approved_by.get_full_name() if a.approved_by else '—',
        a.created_at.strftime('%d %b %Y %H:%M'),
    ] for a in qs.order_by('-created_at')]

    headers = ['Count #', 'Item', 'Domain', 'Previous Qty', 'Physical Qty', 'Difference', 'Type', 'Adjustment Value', 'Reason', 'Approved By', 'Date/Time']
    return _render_card_report(
        request, 'Inventory Adjustment Report', headers, rows, f'inventory_adjustments_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
    )


def _balance_report(request, title, filename_prefix, balance_type):
    domain_filter, category_filter, _, period_filter = _pic_common_filters(request)
    qs = InventoryPeriodBalance.objects.select_related('period').filter(balance_type=balance_type)
    if domain_filter:
        qs = qs.filter(domain=domain_filter)
    if period_filter:
        qs = qs.filter(period_id=period_filter)
    if category_filter:
        cat_name = InventoryCategory.objects.filter(pk=category_filter).values_list('name', flat=True).first()
        qs = qs.filter(category_name=cat_name)

    rows = [[
        b.period.name, dict(PhysicalCount.Domain.choices).get(b.domain, b.domain),
        b.item_name, b.item_code, b.category_name or '—',
        b.quantity, f"ETB {b.unit_cost:,.2f}", f"ETB {b.valuation:,.2f}",
    ] for b in qs.order_by('period__name', 'item_name')]

    headers = ['Period', 'Domain', 'Item', 'Code', 'Category', 'Quantity', 'Unit Cost', 'Valuation']
    return _render_card_report(
        request, title, headers, rows, filename_prefix,
        show_date_filter=False,
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
        categories=InventoryCategory.objects.order_by('name'), category_filter=category_filter,
        periods=InventoryPeriod.objects.order_by('-start_date'), period_filter=period_filter,
    )


@hms_permission_required('core.view_store_reports')
def report_opening_balance(request):
    return _balance_report(request, 'Opening Balance Report', 'opening_balance', InventoryPeriodBalance.BalanceType.OPENING)


@hms_permission_required('core.view_store_reports')
def report_closing_balance(request):
    return _balance_report(request, 'Closing Balance Report', 'closing_balance', InventoryPeriodBalance.BalanceType.CLOSING)


@hms_permission_required('core.view_store_reports')
def report_annual_inventory(request):
    period_filter = request.GET.get('period', '')
    domain_filter = request.GET.get('domain', '')

    rows = []
    if period_filter:
        opening = {b.item_code: b for b in InventoryPeriodBalance.objects.filter(
            period_id=period_filter, balance_type=InventoryPeriodBalance.BalanceType.OPENING,
        )}
        closing_qs = InventoryPeriodBalance.objects.filter(
            period_id=period_filter, balance_type=InventoryPeriodBalance.BalanceType.CLOSING,
        )
        if domain_filter:
            closing_qs = closing_qs.filter(domain=domain_filter)
        for c in closing_qs.order_by('item_name'):
            o = opening.get(c.item_code)
            rows.append([
                c.item_name, c.item_code, dict(PhysicalCount.Domain.choices).get(c.domain, c.domain),
                o.quantity if o else '—', f"ETB {o.valuation:,.2f}" if o else '—',
                c.quantity, f"ETB {c.valuation:,.2f}",
                (c.quantity - o.quantity) if o else '—',
            ])

    headers = ['Item', 'Code', 'Domain', 'Opening Qty', 'Opening Value', 'Closing Qty', 'Closing Value', 'Net Change']
    return _render_card_report(
        request, 'Annual Inventory Report', headers, rows, f'annual_inventory_{period_filter or "none"}',
        show_date_filter=False,
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
        periods=InventoryPeriod.objects.order_by('-start_date'), period_filter=period_filter,
    )


@hms_permission_required('core.view_store_reports')
def report_stock_variance(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    domain_filter, category_filter, store_location_filter, period_filter = _pic_common_filters(request)

    qs = PhysicalCountLine.objects.select_related('physical_count').filter(
        physical_quantity__isnull=False,
        physical_count__created_at__date__gte=from_date, physical_count__created_at__date__lte=to_date,
    )
    if domain_filter:
        qs = qs.filter(physical_count__domain=domain_filter)
    if store_location_filter:
        qs = qs.filter(physical_count__department_store_id=store_location_filter)
    if period_filter:
        qs = qs.filter(physical_count__period_id=period_filter)

    rows = []
    for l in qs.order_by('-physical_count__created_at'):
        if not l.has_variance:
            continue
        if category_filter:
            cat_name = InventoryCategory.objects.filter(pk=category_filter).values_list('name', flat=True).first()
            if l.category_name != cat_name:
                continue
        rows.append([
            l.physical_count.count_number, l.item_name, l.category_name or '—',
            l.system_quantity, l.physical_quantity, l.variance,
            f"ETB {l.adjustment_value:,.2f}",
            'Overage' if l.variance > 0 else 'Shortage',
        ])

    headers = ['Count #', 'Item', 'Category', 'System Qty', 'Physical Qty', 'Variance', 'Adjustment Value', 'Type']
    return _render_card_report(
        request, 'Stock Variance Report', headers, rows, f'stock_variance_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        **_pic_filter_kwargs(domain_filter, category_filter, store_location_filter, period_filter),
    )


@hms_permission_required('core.view_store_reports')
def report_inventory_valuation_combined(request):
    domain_filter = request.GET.get('domain', '')
    category_filter = request.GET.get('category', '')

    rows = []
    if not domain_filter or domain_filter == PhysicalCount.Domain.GENERAL_STORE:
        items_qs = InventoryItem.objects.filter(is_active=True).select_related('category')
        if category_filter:
            items_qs = items_qs.filter(category_id=category_filter)
        for item in items_qs:
            rows.append([
                'General Store', item.name, item.sku, item.category.name if item.category else '—',
                item.quantity_in_stock, f"ETB {item.unit_cost:,.2f}", f"ETB {item.inventory_value:,.2f}",
            ])
    if not domain_filter or domain_filter == PhysicalCount.Domain.MEDICATION:
        med_qs = Medication.objects.filter(inventory_item__is_active=True).select_related('inventory_item__category')
        if category_filter:
            med_qs = med_qs.filter(inventory_item__category_id=category_filter)
        for med in med_qs:
            rows.append([
                'Medication', med.name, med.code, med.category.name if med.category else '—',
                med.current_stock, f"ETB {med.purchase_price:,.2f}", f"ETB {med.inventory_value:,.2f}",
            ])

    headers = ['Domain', 'Item', 'Code', 'Category', 'Quantity', 'Unit Cost', 'Valuation']
    return _render_card_report(
        request, 'Inventory Valuation Report', headers, rows, 'inventory_valuation_combined',
        show_date_filter=False,
        domains=PhysicalCount.Domain.choices, domain_filter=domain_filter,
        categories=InventoryCategory.objects.order_by('name'), category_filter=category_filter,
    )


@hms_permission_required('core.view_store_reports')
def report_inventory_count_history(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    domain_filter, category_filter, store_location_filter, period_filter = _pic_common_filters(request)
    status_filter = request.GET.get('status', '')

    qs = PhysicalCount.objects.select_related('created_by', 'approved_by', 'period', 'department_store').filter(
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    )
    if domain_filter:
        qs = qs.filter(domain=domain_filter)
    if store_location_filter:
        qs = qs.filter(department_store_id=store_location_filter)
    if period_filter:
        qs = qs.filter(period_id=period_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    rows = [[
        c.count_number, c.get_count_type_display(), c.get_domain_display(),
        c.department_store.name if c.department_store else '—',
        c.period.name if c.period else '—', c.status, c.item_count,
        c.created_by.get_full_name() if c.created_by else '—',
        c.created_at.strftime('%d %b %Y'),
        c.completed_at.strftime('%d %b %Y') if c.completed_at else '—',
    ] for c in qs.order_by('-created_at')]

    headers = ['Count #', 'Type', 'Domain', 'Store', 'Period', 'Status', 'Items', 'Started By', 'Started', 'Completed']
    return _render_card_report(
        request, 'Inventory Count History', headers, rows, f'inventory_count_history_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        **_pic_filter_kwargs(domain_filter, category_filter, store_location_filter, period_filter),
    )


@hms_permission_required('core.read_audit_log')
def report_inventory_audit(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = AuditLog.objects.filter(
        module=AuditLog.Module.INVENTORY,
        timestamp__date__gte=from_date, timestamp__date__lte=to_date,
    ).select_related('user')

    rows = [[
        a.timestamp.strftime('%d %b %Y %H:%M'), a.user_name or (a.user.get_full_name() if a.user else '—'),
        a.get_action_display(), a.description,
    ] for a in qs.order_by('-timestamp')]

    headers = ['Date/Time', 'User', 'Action', 'Description']
    return _render_card_report(
        request, 'Inventory Audit Report', headers, rows, f'inventory_audit_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ADMISSION MANAGEMENT REPORTS
# ══════════════════════════════════════════════════════════════════════════════

@hms_permission_required('core.view_admission_reports')
def report_admission_register(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')

    qs = AdmissionRequest.objects.select_related(
        'patient', 'admitting_department', 'admitting_doctor', 'requested_by',
    ).filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
    if dept_filter:
        qs = qs.filter(admitting_department_id=dept_filter)
    qs = qs.order_by('-created_at')

    rows = [[
        r.patient.full_name, r.patient.card_number, r.admission_diagnosis,
        r.admitting_department.name if r.admitting_department else '—',
        f'{r.admitting_doctor.first_name} {r.admitting_doctor.last_name}' if r.admitting_doctor else '—',
        r.get_priority_display(), r.get_request_source_display(), r.get_status_display(),
        r.requested_by.get_full_name() or r.requested_by.username,
        timezone.localtime(r.created_at).strftime('%d %b %Y %H:%M'),
    ] for r in qs]

    headers = ['Patient', 'MRN', 'Diagnosis', 'Department', 'Doctor', 'Priority', 'Source', 'Status', 'Requested By', 'Requested At']
    return _render_card_report(
        request, 'Admission Register', headers, rows, f'admission_register_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        departments=Department.objects.filter(is_active=True), dept_filter=dept_filter,
    )


@hms_permission_required('core.view_admission_reports')
def report_daily_admissions(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')

    qs = Admission.objects.select_related('patient', 'bed__room__ward__department', 'admitted_by').filter(
        admitted_at__date__gte=from_date, admitted_at__date__lte=to_date,
    )
    if dept_filter:
        qs = qs.filter(bed__room__ward__department_id=dept_filter)
    qs = qs.order_by('-admitted_at')

    rows = [[
        timezone.localtime(a.admitted_at).strftime('%d %b %Y'), a.patient.full_name, a.patient.card_number,
        a.bed.room.ward.name, a.bed.bed_code, a.get_priority_display(),
        a.admitted_by.get_full_name() or a.admitted_by.username,
    ] for a in qs]

    headers = ['Date', 'Patient', 'MRN', 'Ward', 'Bed', 'Priority', 'Admitted By']
    return _render_card_report(
        request, 'Daily Admission Report', headers, rows, f'daily_admissions_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        departments=Department.objects.filter(is_active=True), dept_filter=dept_filter,
    )


@hms_permission_required('core.view_admission_reports')
def report_length_of_stay(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = Admission.objects.select_related('patient', 'bed__room__ward').filter(
        discharged_at__isnull=False, discharged_at__date__gte=from_date, discharged_at__date__lte=to_date,
        status=Admission.Status.DISCHARGED,
    ).order_by('-discharged_at')

    rows = []
    for a in qs:
        los_days = round((a.discharged_at - a.admitted_at).total_seconds() / 86400, 1)
        rows.append([
            a.patient.full_name, a.patient.card_number, a.bed.room.ward.name,
            timezone.localtime(a.admitted_at).strftime('%d %b %Y'),
            timezone.localtime(a.discharged_at).strftime('%d %b %Y'),
            los_days,
        ])

    headers = ['Patient', 'MRN', 'Ward', 'Admitted', 'Discharged', 'Length of Stay (days)']
    return _render_card_report(
        request, 'Length of Stay Report', headers, rows, f'length_of_stay_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_admission_reports')
def report_deposit_collection(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_filter = request.GET.get('status', '')

    qs = InvoiceItem.objects.filter(
        service_type=InvoiceItem.ServiceType.DEPOSIT,
        invoice__created_at__date__gte=from_date, invoice__created_at__date__lte=to_date,
    ).select_related('invoice__patient', 'credit_approved_by').order_by('-invoice__created_at')
    if status_filter:
        qs = qs.filter(payment_status=status_filter)

    rows = [[
        timezone.localtime(i.invoice.created_at).strftime('%d %b %Y'), i.invoice.patient.full_name,
        i.invoice.patient.card_number, f'ETB {i.total:,.2f}', i.payment_status,
        f'ETB {i.paid_amount:,.2f}', i.credit_approved_by.get_full_name() if i.credit_approved_by else '—',
    ] for i in qs]

    headers = ['Date', 'Patient', 'MRN', 'Deposit Amount', 'Status', 'Paid Amount', 'Credit Approved By']
    return _render_card_report(
        request, 'Deposit Collection Report', headers, rows, f'deposit_collection_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_admission_reports')
def report_admission_revenue(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = InvoiceItem.objects.filter(
        invoice__visit__admissions__isnull=False,
        invoice__created_at__date__gte=from_date, invoice__created_at__date__lte=to_date,
    ).exclude(payment_status__in=[InvoiceItem.PaymentStatus.CANCELLED]).distinct().select_related('invoice__patient')

    by_service = qs.values('service_type').annotate(total=Sum('total'), count=Count('id')).order_by('-total')
    rows = [[s['service_type'], s['count'], f"ETB {s['total']:,.2f}"] for s in by_service]

    headers = ['Service Type', 'Count', 'Total Revenue']
    return _render_card_report(
        request, 'Admission Revenue Report', headers, rows, f'admission_revenue_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_admission_reports')
def report_department_admission_stats(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = Admission.objects.filter(
        admitted_at__date__gte=from_date, admitted_at__date__lte=to_date,
    ).values('bed__room__ward__department__name').annotate(count=Count('id')).order_by('-count')

    rows = [[d['bed__room__ward__department__name'] or 'Unassigned', d['count']] for d in qs]

    headers = ['Department', 'Admissions']
    return _render_card_report(
        request, 'Department Admission Statistics', headers, rows, f'dept_admission_stats_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_admission_reports')
def report_readmissions(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    from datetime import timedelta

    lookback_days = int(request.GET.get('lookback_days', 30) or 30)
    admissions = list(
        Admission.objects.filter(admitted_at__date__gte=from_date, admitted_at__date__lte=to_date)
        .select_related('patient').order_by('patient_id', 'admitted_at')
    )
    prior_discharges = {}
    rows = []
    for a in Admission.objects.filter(status=Admission.Status.DISCHARGED, discharged_at__isnull=False).order_by('patient_id', 'discharged_at'):
        prior_discharges.setdefault(a.patient_id, []).append(a.discharged_at)

    for a in admissions:
        for d_at in prior_discharges.get(a.patient_id, []):
            if d_at < a.admitted_at and (a.admitted_at - d_at) <= timedelta(days=lookback_days):
                rows.append([
                    a.patient.full_name, a.patient.card_number,
                    timezone.localtime(d_at).strftime('%d %b %Y'),
                    timezone.localtime(a.admitted_at).strftime('%d %b %Y'),
                    (a.admitted_at - d_at).days,
                ])
                break

    headers = ['Patient', 'MRN', 'Prior Discharge', 'Readmitted', 'Days Between']
    return _render_card_report(
        request, 'Readmission Report', headers, rows, f'readmissions_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_admission_reports')
def report_admission_cancellations(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = AdmissionRequest.objects.select_related('patient', 'requested_by').filter(
        status__in=[AdmissionRequest.Status.REJECTED, AdmissionRequest.Status.CANCELLED],
        created_at__date__gte=from_date, created_at__date__lte=to_date,
    ).order_by('-created_at')

    rows = [[
        r.patient.full_name, r.patient.card_number, r.get_status_display(),
        r.rejection_reason or '—', r.requested_by.get_full_name() or r.requested_by.username,
        timezone.localtime(r.created_at).strftime('%d %b %Y'),
    ] for r in qs]

    headers = ['Patient', 'MRN', 'Status', 'Reason', 'Requested By', 'Requested At']
    return _render_card_report(
        request, 'Admission Cancellation Report', headers, rows, f'admission_cancellations_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ATTACHMENT / DOCUMENT MANAGEMENT REPORTS
# ══════════════════════════════════════════════════════════════════════════════

def _attachment_base_qs(request):
    qs = PatientAttachment.objects.select_related('patient', 'category', 'uploaded_by', 'department').filter(
        is_current=True, is_deleted=False,
    )
    if not request.user.has_perm('core.view_confidential_attachments'):
        qs = qs.exclude(is_confidential=True)
    return qs


@hms_permission_required('core.view_attachment_reports')
def report_attachment_history(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    patient_q = request.GET.get('patient', '').strip()

    qs = _attachment_base_qs(request).filter(uploaded_at__date__gte=from_date, uploaded_at__date__lte=to_date)
    if patient_q:
        qs = qs.filter(
            Q(patient__first_name__icontains=patient_q) | Q(patient__last_name__icontains=patient_q)
            | Q(patient__card_number__icontains=patient_q)
        )
    qs = qs.order_by('patient_id', '-uploaded_at')

    rows = [[
        r.patient.full_name, r.patient.card_number, r.title, r.category.name,
        r.uploaded_by.get_full_name() or r.uploaded_by.username,
        r.department.name if r.department else '—',
        timezone.localtime(r.uploaded_at).strftime('%d %b %Y %H:%M'),
        'Yes' if r.is_confidential else 'No',
    ] for r in qs]

    headers = ['Patient', 'MRN', 'Title', 'Category', 'Uploaded By', 'Department', 'Uploaded At', 'Confidential']
    return _render_card_report(
        request, 'Patient Attachment History', headers, rows, f'attachment_history_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_attachment_reports')
def report_attachments_by_category(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    category_f = request.GET.get('category', '')

    qs = _attachment_base_qs(request).filter(uploaded_at__date__gte=from_date, uploaded_at__date__lte=to_date)
    if category_f:
        qs = qs.filter(category_id=category_f)

    by_cat = qs.values('category__name').annotate(count=Count('id')).order_by('-count')
    rows = [[c['category__name'] or '—', c['count']] for c in by_cat]

    headers = ['Category', 'Attachment Count']
    return _render_card_report(
        request, 'Attachments by Category', headers, rows, f'attachments_by_category_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
        categories=AttachmentCategory.objects.filter(is_active=True), category_filter=category_f,
    )


@hms_permission_required('core.view_attachment_reports')
def report_attachments_by_department(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)

    qs = _attachment_base_qs(request).filter(uploaded_at__date__gte=from_date, uploaded_at__date__lte=to_date)
    by_dept = qs.values('department__name').annotate(count=Count('id')).order_by('-count')
    rows = [[d['department__name'] or 'Unassigned', d['count']] for d in by_dept]

    headers = ['Department', 'Attachment Count']
    return _render_card_report(
        request, 'Attachments by Department', headers, rows, f'attachments_by_department_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_attachment_reports')
def report_missing_required_documents(request):
    """Cross-references currently-admitted patients against attachment
    categories flagged is_required_for_admission, listing gaps."""
    required_categories = list(AttachmentCategory.objects.filter(is_active=True, is_required_for_admission=True))
    admitted = Admission.objects.filter(status=Admission.Status.ADMITTED).select_related('patient')

    rows = []
    for adm in admitted:
        have = set(
            _attachment_base_qs(request).filter(patient=adm.patient).values_list('category_id', flat=True)
        )
        missing = [c.name for c in required_categories if c.id not in have]
        if missing:
            rows.append([adm.patient.full_name, adm.patient.card_number, adm.bed.bed_code, ', '.join(missing)])

    headers = ['Patient', 'MRN', 'Bed', 'Missing Required Documents']
    return _render_card_report(
        request, 'Missing Required Documents Report', headers, rows, 'missing_required_documents',
        show_date_filter=False,
    )


@hms_permission_required('core.view_attachment_reports')
def report_recent_attachments(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    qs = _attachment_base_qs(request).filter(
        uploaded_at__date__gte=from_date, uploaded_at__date__lte=to_date,
    ).order_by('-uploaded_at')[:200]

    rows = [[
        timezone.localtime(r.uploaded_at).strftime('%d %b %Y %H:%M'), r.patient.full_name,
        r.title, r.category.name, r.uploaded_by.get_full_name() or r.uploaded_by.username,
    ] for r in qs]

    headers = ['Uploaded At', 'Patient', 'Title', 'Category', 'Uploaded By']
    return _render_card_report(
        request, 'Recently Uploaded Documents', headers, rows, f'recent_attachments_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_confidential_attachments')
def report_confidential_attachments(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    qs = PatientAttachment.objects.select_related('patient', 'category', 'uploaded_by').filter(
        is_confidential=True, is_current=True, is_deleted=False,
        uploaded_at__date__gte=from_date, uploaded_at__date__lte=to_date,
    ).order_by('-uploaded_at')

    rows = [[
        r.patient.full_name, r.patient.card_number, r.title, r.category.name,
        r.uploaded_by.get_full_name() or r.uploaded_by.username,
        timezone.localtime(r.uploaded_at).strftime('%d %b %Y %H:%M'),
    ] for r in qs]

    headers = ['Patient', 'MRN', 'Title', 'Category', 'Uploaded By', 'Uploaded At']
    return _render_card_report(
        request, 'Confidential Documents Report', headers, rows, f'confidential_attachments_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )


@hms_permission_required('core.view_attachment_reports')
def report_attachment_activity_log(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    qs = AuditLog.objects.filter(
        module=AuditLog.Module.DOCUMENT, timestamp__date__gte=from_date, timestamp__date__lte=to_date,
    ).select_related('user').order_by('-timestamp')

    rows = [[
        a.timestamp.strftime('%d %b %Y %H:%M'), a.user_name or (a.user.get_full_name() if a.user else '—'),
        a.get_action_display(), a.description,
    ] for a in qs]

    headers = ['Date/Time', 'User', 'Action', 'Description']
    return _render_card_report(
        request, 'Attachment Activity Log', headers, rows, f'attachment_activity_{from_str}_{to_str}',
        from_date=from_date, to_date=to_date, from_str=from_str, to_str=to_str,
    )
