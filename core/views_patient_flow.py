from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Department, DischargeSummary, Invoice, Patient,
    Visit, VisitJourneyEvent,
)
from .patient_flow import log_journey_event

User = get_user_model()

# ── Status metadata ───────────────────────────────────────────────────────────
STATUS_META = {
    Visit.Status.REGISTERED:             {'color': 'slate',  'icon': '🏥', 'step': 1},
    Visit.Status.VISIT_CREATED:          {'color': 'sky',    'icon': '📋', 'step': 2},
    Visit.Status.WAITING_PAYMENT:        {'color': 'amber',  'icon': '💳', 'step': 3},
    Visit.Status.PAYMENT_COMPLETED:      {'color': 'green',  'icon': '✅', 'step': 4},
    Visit.Status.WAITING_DOCTOR:         {'color': 'blue',   'icon': '⏳', 'step': 5},
    Visit.Status.CONSULTATION_STARTED:   {'color': 'indigo', 'icon': '👨‍⚕️', 'step': 6},
    Visit.Status.INVESTIGATION_ORDERED:  {'color': 'purple', 'icon': '🔬', 'step': 7},
    Visit.Status.INVESTIGATION_COMPLETED:{'color': 'violet', 'icon': '📊', 'step': 8},
    Visit.Status.TREATMENT_STARTED:      {'color': 'teal',   'icon': '💊', 'step': 9},
    Visit.Status.PROCEDURE_SCHEDULED:    {'color': 'orange', 'icon': '🔪', 'step': 10},
    Visit.Status.COMPLETED:              {'color': 'emerald','icon': '🏁', 'step': 11},
    Visit.Status.DISCHARGED:             {'color': 'green',  'icon': '🚶', 'step': 12},
    Visit.Status.FOLLOW_UP_REQUIRED:     {'color': 'rose',   'icon': '📅', 'step': 13},
}


def _status_css(status):
    colors = {
        'slate':   'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
        'sky':     'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
        'amber':   'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
        'green':   'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
        'blue':    'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
        'indigo':  'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300',
        'purple':  'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
        'violet':  'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
        'teal':    'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
        'orange':  'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
        'emerald': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
        'rose':    'bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300',
    }
    meta = STATUS_META.get(status, {'color': 'slate'})
    return colors.get(meta['color'], colors['slate'])


# ── Patient Flow Dashboard ────────────────────────────────────────────────────

@hms_permission_required('core.view_patient_flow')
def patient_flow_dashboard(request):
    today = timezone.localdate()

    # All active visits today (not discharged)
    today_visits = Visit.objects.filter(
        created_at__date=today
    ).select_related('patient', 'doctor', 'department').order_by('-created_at')

    # KPIs
    total_today      = today_visits.count()
    waiting_payment  = today_visits.filter(status=Visit.Status.WAITING_PAYMENT).count()
    waiting_doctor   = today_visits.filter(status=Visit.Status.WAITING_DOCTOR).count()
    in_consultation  = today_visits.filter(status=Visit.Status.CONSULTATION_STARTED).count()
    discharged_today = today_visits.filter(status=Visit.Status.DISCHARGED).count()
    active_count     = today_visits.exclude(
        status__in=[Visit.Status.DISCHARGED, Visit.Status.COMPLETED]
    ).count()

    # Group visits by status
    by_status = {}
    for s_val, s_label in Visit.Status.choices:
        qs = today_visits.filter(status=s_val)
        if qs.exists():
            by_status[s_label] = {
                'visits': qs[:20],
                'count':  qs.count(),
                'css':    _status_css(s_val),
                'value':  s_val,
            }

    # Recent journey events (last 50)
    recent_events = VisitJourneyEvent.objects.select_related(
        'visit__patient', 'performed_by'
    ).order_by('-timestamp')[:50]

    # Dept workload
    dept_stats = (
        today_visits.exclude(status__in=[Visit.Status.DISCHARGED, Visit.Status.COMPLETED])
        .values('department__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    return render(request, 'patient_flow/dashboard.html', {
        'today':          today,
        'total_today':    total_today,
        'waiting_payment':waiting_payment,
        'waiting_doctor': waiting_doctor,
        'in_consultation':in_consultation,
        'discharged_today':discharged_today,
        'active_count':   active_count,
        'by_status':      by_status,
        'recent_events':  recent_events,
        'dept_stats':     dept_stats,
        'status_choices': Visit.Status.choices,
    })


# ── All Active Patients List ──────────────────────────────────────────────────

@hms_permission_required('core.view_patient_flow')
def patient_flow_list(request):
    qs = Visit.objects.select_related('patient', 'doctor', 'department').order_by('-created_at')

    # Filters
    status_f = request.GET.get('status', '')
    dept_f   = request.GET.get('dept', '')
    date_f   = request.GET.get('date', str(timezone.localdate()))
    q        = request.GET.get('q', '')

    if status_f:
        qs = qs.filter(status=status_f)
    if dept_f:
        qs = qs.filter(department_id=dept_f)
    if date_f:
        qs = qs.filter(created_at__date=date_f)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__mrn__icontains=q) |
            Q(patient__card_number__icontains=q)
        )

    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    departments = Department.objects.filter(is_active=True)

    return render(request, 'patient_flow/list.html', {
        'page_obj':       page_obj,
        'status_choices': Visit.Status.choices,
        'departments':    departments,
        'status_f':       status_f,
        'dept_f':         dept_f,
        'date_f':         date_f,
        'q':              q,
        'status_css_map': {v: _status_css(v) for v, _ in Visit.Status.choices},
    })


# ── Patient Journey Timeline ──────────────────────────────────────────────────

@hms_permission_required('core.view_patient_flow')
def patient_journey_detail(request, visit_id):
    visit  = get_object_or_404(
        Visit.objects.select_related('patient', 'doctor', 'department'), pk=visit_id
    )
    events = visit.journey_events.select_related('performed_by').order_by('timestamp')

    # Related clinical data
    try:
        from .models import (
            ClinicalNote, Diagnosis, ImagingOrder, LabOrder,
            MedicationOrder, SurgeryOrder, VitalSign,
        )
        vitals    = VitalSign.objects.filter(visit=visit).order_by('recorded_at')
        diagnoses = Diagnosis.objects.filter(visit=visit)
        lab_orders = LabOrder.objects.filter(visit=visit).order_by('-ordered_at')
        imaging_orders = ImagingOrder.objects.filter(visit=visit).order_by('-ordered_at')
        med_orders = MedicationOrder.objects.filter(visit=visit).order_by('-ordered_at')
        surgery_orders = SurgeryOrder.objects.filter(visit=visit).order_by('-ordered_at')
        notes = ClinicalNote.objects.filter(visit=visit).order_by('-created_at')
    except Exception:
        vitals = diagnoses = lab_orders = imaging_orders = med_orders = surgery_orders = notes = []

    discharge = getattr(visit, 'discharge_summary', None)
    invoice   = Invoice.objects.filter(visit=visit).first()

    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.PATIENT,
        object_type='Visit', object_id=visit.pk,
        object_repr=str(visit.patient),
        description=f'Viewed patient journey for {visit.patient}',
        request=request,
    )

    return render(request, 'patient_flow/journey.html', {
        'visit':          visit,
        'events':         events,
        'discharge':      discharge,
        'invoice':        invoice,
        'vitals':         vitals,
        'diagnoses':      diagnoses,
        'lab_orders':     lab_orders,
        'imaging_orders': imaging_orders,
        'med_orders':     med_orders,
        'surgery_orders': surgery_orders,
        'notes':          notes,
        'status_css':     _status_css(visit.status),
        'status_meta':    STATUS_META,
        'status_choices': Visit.Status.choices,
    })


# ── Manual Status Update ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_patient_flow')
@require_POST
def visit_status_update(request, visit_id):
    visit      = get_object_or_404(Visit, pk=visit_id)
    new_status = request.POST.get('status', '').strip()
    note       = request.POST.get('note', '').strip()

    valid_statuses = [v for v, _ in Visit.Status.choices]
    if new_status not in valid_statuses:
        messages.error(request, 'Invalid status.')
        return redirect(request.META.get('HTTP_REFERER', 'patient_flow_dashboard'))

    old_label = visit.get_status_display()
    log_journey_event(
        visit, f'Status manually updated to: {dict(Visit.Status.choices)[new_status]}',
        event_type='status_change',
        description=note,
        new_status=new_status,
        performed_by=request.user,
    )
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.PATIENT,
        object_type='Visit', object_id=visit.pk,
        object_repr=str(visit.patient),
        description=f'Visit status updated: {old_label} → {visit.get_status_display()}',
        request=request,
    )
    messages.success(request, f'Status updated to "{visit.get_status_display()}".')
    return redirect(request.META.get('HTTP_REFERER', 'patient_flow_dashboard'))


# ── Discharge Workflow ────────────────────────────────────────────────────────

@hms_permission_required('core.manage_discharge')
def discharge_create(request, visit_id):
    visit    = get_object_or_404(Visit.objects.select_related('patient', 'doctor'), pk=visit_id)
    existing = getattr(visit, 'discharge_summary', None)
    doctors  = User.objects.filter(
        Q(groups__name__in=['Doctor', 'Ward Doctor', 'Surgeon', 'Emergency Doctor', 'Medical Director'])
    ).distinct().order_by('last_name', 'first_name')

    if request.method == 'POST':
        p = request.POST
        try:
            data = dict(
                discharge_type          = p.get('discharge_type', 'regular'),
                discharge_condition     = p.get('discharge_condition', 'improved'),
                final_diagnosis         = p['final_diagnosis'].strip(),
                secondary_diagnoses     = p.get('secondary_diagnoses', '').strip(),
                procedures_performed    = p.get('procedures_performed', '').strip(),
                hospital_course         = p.get('hospital_course', '').strip(),
                treatment_summary       = p.get('treatment_summary', '').strip(),
                investigations_summary  = p.get('investigations_summary', '').strip(),
                discharge_instructions  = p.get('discharge_instructions', '').strip(),
                diet_instructions       = p.get('diet_instructions', '').strip(),
                activity_restrictions   = p.get('activity_restrictions', '').strip(),
                wound_care              = p.get('wound_care', '').strip(),
                follow_up_date          = p.get('follow_up_date') or None,
                follow_up_doctor_id     = p.get('follow_up_doctor') or None,
                follow_up_instructions  = p.get('follow_up_instructions', '').strip(),
                discharge_medications   = p.get('discharge_medications', '').strip(),
                transfer_facility       = p.get('transfer_facility', '').strip(),
                transfer_reason         = p.get('transfer_reason', '').strip(),
                authored_by             = request.user,
            )
            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
                existing.save()
                ds = existing
            else:
                ds = DischargeSummary.objects.create(visit=visit, **data)

            # Determine new visit status
            follow_up = bool(data['follow_up_date'])
            new_status = Visit.Status.FOLLOW_UP_REQUIRED if follow_up else Visit.Status.DISCHARGED

            log_journey_event(
                visit,
                'Discharge summary completed' + (' — follow-up required' if follow_up else ''),
                event_type='discharge',
                description=f'Condition: {data["discharge_condition"]}. {data["final_diagnosis"][:100]}',
                new_status=new_status,
                performed_by=request.user,
            )
            log_action(
                request.user, AuditLog.Action.CREATE if not existing else AuditLog.Action.UPDATE,
                AuditLog.Module.PATIENT,
                object_type='DischargeSummary', object_id=ds.pk,
                object_repr=str(visit.patient),
                description=f'Discharge summary {"created" if not existing else "updated"} for {visit.patient}',
                request=request,
            )
            messages.success(request, 'Discharge summary saved.')
            return redirect('patient_journey_detail', visit_id=visit.pk)
        except KeyError as exc:
            messages.error(request, f'Required field missing: {exc}')

    return render(request, 'patient_flow/discharge_form.html', {
        'visit':               visit,
        'ds':                  existing,
        'doctors':             doctors,
        'discharge_types':     DischargeSummary.DischargeType.choices,
        'discharge_conditions':DischargeSummary.DischargeCondition.choices,
    })


# ── Department Work List ──────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_worklist')
def dept_worklist(request):
    """Per-department view of pending patient tasks."""
    user = request.user
    today = timezone.localdate()

    # Determine which department context the user is in
    dept_id = request.GET.get('dept', '')
    try:
        user_dept = user.userprofile.department
    except Exception:
        user_dept = None

    if dept_id:
        dept = get_object_or_404(Department, pk=dept_id)
    elif user_dept:
        dept = user_dept
    else:
        dept = None

    # Work items per role
    from .models import ImagingOrder, LabOrder, MedicationOrder, SurgeryOrder

    context = {
        'dept': dept,
        'today': today,
        'departments': Department.objects.filter(is_active=True),
        'dept_id': dept_id,
    }

    # Lab pending
    context['pending_lab'] = LabOrder.objects.filter(
        status__in=['ordered', 'processing']
    ).select_related('visit__patient', 'ordered_by').order_by('-ordered_at')[:30]

    # Radiology pending
    context['pending_imaging'] = ImagingOrder.objects.filter(
        status__in=['ordered', 'in_progress']
    ).select_related('visit__patient', 'ordered_by').order_by('-ordered_at')[:30]

    # Pharmacy pending
    context['pending_meds'] = MedicationOrder.objects.filter(
        status__in=['ordered', 'active']
    ).select_related('visit__patient', 'prescribed_by').order_by('-ordered_at')[:30]

    # Surgery pending
    context['pending_surgery'] = SurgeryOrder.objects.filter(
        status__in=['ordered', 'pending_review', 'approved', 'scheduled']
    ).select_related('patient', 'surgeon').order_by('-ordered_at')[:20]

    # Patients awaiting payment
    context['awaiting_payment'] = Visit.objects.filter(
        status=Visit.Status.WAITING_PAYMENT,
        created_at__date=today,
    ).select_related('patient', 'doctor').order_by('created_at')[:30]

    # Patients waiting for doctor
    context['waiting_doctor'] = Visit.objects.filter(
        status=Visit.Status.WAITING_DOCTOR,
        created_at__date=today,
    ).select_related('patient', 'doctor', 'department').order_by('created_at')[:30]

    return render(request, 'patient_flow/dept_worklist.html', context)


# ── Quick Stats API (used by dashboard) ──────────────────────────────────────

@hms_permission_required('core.view_patient_flow')
def flow_stats_api(request):
    from django.http import JsonResponse
    today = timezone.localdate()
    counts = {}
    for s_val, s_label in Visit.Status.choices:
        counts[s_val] = Visit.objects.filter(status=s_val, created_at__date=today).count()
    return JsonResponse({'date': str(today), 'by_status': counts})
