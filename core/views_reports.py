"""
HMS Reporting Module — central views for all management reports.
Every view supports HTML viewing, Excel export (?export=excel),
CSV export (?export=csv), and browser-print/PDF (window.print()).
"""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.shortcuts import render
from django.utils import timezone

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog,
    Attendance,
    ClinicalNote,
    Department,
    Diagnosis,
    Doctor,
    Employee,
    ImagingOrder,
    Invoice,
    InvoiceItem,
    LabOrder,
    MedicationOrder,
    Patient,
    Payment,
    PharmacySale,
    PharmacySaleItem,
    PrescriptionItem,
    ProcedureOrder,
    Prescription,
    Visit,
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

    return render(request, 'reports/hub.html', {
        'today': today,
        'stats': stats,
        'can_financial': can_financial,
        'can_dept': can_dept,
        'can_audit': can_audit,
        'can_hr': can_hr,
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


# ── Doctor Performance Report ─────────────────────────────────────────────────

@hms_permission_required('core.read_department_reports')
def report_doctor_performance(request):
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    dept_filter = request.GET.get('department', '')
    q           = request.GET.get('q', '').strip()
    export      = request.GET.get('export', '')

    doctors_qs = Doctor.objects.select_related('department').filter(
        visits__created_at__date__gte=from_date,
        visits__created_at__date__lte=to_date,
    ).distinct()

    if dept_filter:
        doctors_qs = doctors_qs.filter(department__id=dept_filter)
    if q:
        doctors_qs = doctors_qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )

    doctors_qs = doctors_qs.annotate(
        total_visits=Count('visits', distinct=True,
                           filter=Q(visits__created_at__date__gte=from_date,
                                    visits__created_at__date__lte=to_date)),
        new_patients=Count('visits__patient', distinct=True,
                           filter=Q(visits__visit_type=Visit.VisitType.NEW_VISIT,
                                    visits__created_at__date__gte=from_date,
                                    visits__created_at__date__lte=to_date)),
        revisits=Count('visits', distinct=True,
                       filter=Q(visits__visit_type=Visit.VisitType.REVISIT,
                                visits__created_at__date__gte=from_date,
                                visits__created_at__date__lte=to_date)),
        notes_written=Count('visits__clinical_notes', distinct=True,
                            filter=Q(visits__created_at__date__gte=from_date,
                                     visits__created_at__date__lte=to_date)),
        diagnoses_made=Count('visits__diagnoses', distinct=True,
                             filter=Q(visits__created_at__date__gte=from_date,
                                      visits__created_at__date__lte=to_date)),
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

    paginator = Paginator(list(doctors_qs), 30)
    page_obj  = paginator.get_page(request.GET.get('page', 1))
    departments = Department.objects.filter(is_active=True).order_by('name')

    return render(request, 'reports/doctor_performance.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'dept_filter': dept_filter, 'q': q,
        'page_obj': page_obj, 'departments': departments,
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
    from_date, to_date, from_str, to_str = _parse_date_range(request)
    status_f = request.GET.get('status', '')
    export   = request.GET.get('export', '')

    qs = PharmacySale.objects.filter(
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).select_related('patient', 'cashier')

    if status_f:
        qs = qs.filter(status=status_f)

    totals = qs.aggregate(
        revenue=Sum('total_amount'),
        paid=Sum('paid_amount'),
        discount=Sum('discount_amount'),
        count=Count('id'),
    )

    method_summary = (
        qs.values('payment_method')
        .annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('-total')
    )

    medication_summary = (
        PharmacySaleItem.objects.filter(sale__in=qs)
        .values('medication__name', 'medication__generic_name')
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total_price'),
        )
        .order_by('-qty_sold')[:30]
    )

    daily = (
        qs.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'), revenue=Sum('total_amount'))
        .order_by('day')
    )

    if export in ('excel', 'csv'):
        headers = ['Sale #', 'Date', 'Customer', 'Type', 'Status', 'Payment Method',
                   'Subtotal', 'Discount', 'Total', 'Paid']
        rows = []
        for s in qs.values('sale_number', 'created_at', 'customer_name',
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
        period = _period_label(from_date, to_date)
        if export == 'excel':
            r = excel_response(f'pharmacy_sales_{from_str}_{to_str}.xlsx')
            wb = build_workbook('Pharmacy Sales Report', period, _generated_by(request), headers, rows)
            return send_workbook(wb, r)
        r = csv_response(f'pharmacy_sales_{from_str}_{to_str}.csv')
        return write_csv(r, headers, rows)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'reports/pharmacy_summary.html', {
        'from_date': from_date, 'to_date': to_date,
        'from_date_str': from_str, 'to_date_str': to_str,
        'status_f': status_f,
        'totals': totals,
        'method_summary': method_summary,
        'medication_summary': medication_summary,
        'daily': list(daily),
        'page_obj': page_obj,
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
        .values('medication_name')
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
