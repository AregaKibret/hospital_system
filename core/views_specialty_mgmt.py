"""
Specialty Assignment & Referral Management
- Internal specialty referrals
- Specialty-based reports
"""
import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Doctor, Patient, Specialization, SpecialtyReferral, Visit,
)


# ── AJAX helpers ──────────────────────────────────────────────────────────────

def get_doctor_specialty(request):
    """Return primary specialization id/name for a given doctor_id."""
    doctor_id = request.GET.get('doctor_id', '')
    try:
        doc = Doctor.objects.select_related('specialization').get(pk=doctor_id, active=True)
    except Doctor.DoesNotExist:
        return JsonResponse({'specialty_id': None, 'specialty_name': ''})
    if doc.specialization:
        return JsonResponse({'specialty_id': doc.specialization_id, 'specialty_name': doc.specialization.name})
    return JsonResponse({'specialty_id': None, 'specialty_name': ''})


def get_doctors_for_specialty(request):
    """Return doctors that have a given specialty as primary or secondary."""
    spec_id = request.GET.get('specialty_id', '')
    if not spec_id:
        return JsonResponse({'doctors': []})
    docs = Doctor.objects.filter(
        active=True
    ).filter(
        Q(specialization_id=spec_id) | Q(secondary_specializations__id=spec_id)
    ).select_related('department').distinct().order_by('last_name', 'first_name')
    return JsonResponse({'doctors': [
        {'id': d.pk, 'name': f'Dr. {d.full_name}', 'department': d.department.name}
        for d in docs
    ]})


# ── Referral list ─────────────────────────────────────────────────────────────

@hms_permission_required('core.view_specialty_referral')
def referral_list(request):
    qs = SpecialtyReferral.objects.select_related(
        'patient', 'from_specialty', 'to_specialty', 'from_doctor', 'to_doctor',
    ).order_by('-created_at')

    status_filter = request.GET.get('status', '')
    from_spec = request.GET.get('from_spec', '')
    to_spec = request.GET.get('to_spec', '')
    q = request.GET.get('q', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if from_spec:
        qs = qs.filter(from_specialty_id=from_spec)
    if to_spec:
        qs = qs.filter(to_specialty_id=to_spec)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__card_number__icontains=q)
        )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    specializations = Specialization.objects.filter(is_active=True).order_by('name')

    return render(request, 'specialty_mgmt/referral_list.html', {
        'page_obj': page_obj,
        'specializations': specializations,
        'status_choices': SpecialtyReferral.Status.choices,
        'filters': {'status': status_filter, 'from_spec': from_spec, 'to_spec': to_spec, 'q': q},
    })


# ── Referral create ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_specialty_referral')
def referral_create(request, visit_pk):
    visit = get_object_or_404(Visit.objects.select_related('patient', 'doctor', 'specialty', 'department'), pk=visit_pk)
    specializations = Specialization.objects.filter(is_active=True).order_by('display_order', 'name')
    doctors = Doctor.objects.filter(active=True).select_related('specialization', 'department').order_by('last_name')

    if request.method == 'POST':
        p = request.POST
        from_spec_id = p.get('from_specialty', '').strip()
        to_spec_id = p.get('to_specialty', '').strip()
        to_doctor_id = p.get('to_doctor', '').strip()
        urgency = p.get('urgency', SpecialtyReferral.Urgency.ROUTINE)
        reason = p.get('reason', '').strip()
        clinical_summary = p.get('clinical_summary', '').strip()
        errors = []

        if not from_spec_id:
            errors.append('Referring specialty is required.')
        if not to_spec_id:
            errors.append('Target specialty is required.')
        if not reason:
            errors.append('Reason for referral is required.')
        if from_spec_id and to_spec_id and from_spec_id == to_spec_id:
            errors.append('Referring and target specialties must be different.')

        from_specialty = to_specialty = to_doctor = None
        if from_spec_id:
            try:
                from_specialty = Specialization.objects.get(pk=from_spec_id)
            except Specialization.DoesNotExist:
                errors.append('Referring specialty not found.')
        if to_spec_id:
            try:
                to_specialty = Specialization.objects.get(pk=to_spec_id)
            except Specialization.DoesNotExist:
                errors.append('Target specialty not found.')
        if to_doctor_id:
            try:
                to_doctor = Doctor.objects.get(pk=to_doctor_id)
            except Doctor.DoesNotExist:
                pass

        if not errors:
            referral = SpecialtyReferral.objects.create(
                patient=visit.patient,
                from_visit=visit,
                from_specialty=from_specialty,
                to_specialty=to_specialty,
                from_doctor=visit.doctor if hasattr(visit, 'doctor') else None,
                to_doctor=to_doctor,
                urgency=urgency,
                reason=reason,
                clinical_summary=clinical_summary,
                created_by=request.user,
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.VISIT,
                object_type='SpecialtyReferral', object_id=referral.pk,
                object_repr=str(referral),
                description=f'Referral created: {visit.patient.full_name} from {from_specialty} to {to_specialty}',
                request=request,
            )
            messages.success(request, f'Referral to {to_specialty} created successfully.')
            return redirect('referral_detail', pk=referral.pk)

        for err in errors:
            messages.error(request, err)

    return render(request, 'specialty_mgmt/referral_form.html', {
        'visit': visit,
        'specializations': specializations,
        'doctors': doctors,
        'urgency_choices': SpecialtyReferral.Urgency.choices,
        'post': request.POST if request.method == 'POST' else None,
    })


# ── Referral detail + status actions ─────────────────────────────────────────

@hms_permission_required('core.view_specialty_referral')
def referral_detail(request, pk):
    referral = get_object_or_404(
        SpecialtyReferral.objects.select_related(
            'patient', 'from_visit', 'from_specialty', 'to_specialty',
            'from_doctor', 'to_doctor', 'appointment', 'created_by',
        ),
        pk=pk,
    )
    doctors = Doctor.objects.filter(active=True).select_related('specialization', 'department').order_by('last_name')
    return render(request, 'specialty_mgmt/referral_detail.html', {
        'referral': referral,
        'doctors': doctors,
    })


@hms_permission_required('core.accept_specialty_referral')
def referral_accept(request, pk):
    if request.method != 'POST':
        return redirect('referral_detail', pk=pk)
    referral = get_object_or_404(SpecialtyReferral, pk=pk, status=SpecialtyReferral.Status.PENDING)
    to_doctor_id = request.POST.get('to_doctor', '').strip()
    response_notes = request.POST.get('response_notes', '').strip()
    if to_doctor_id:
        try:
            referral.to_doctor = Doctor.objects.get(pk=to_doctor_id)
        except Doctor.DoesNotExist:
            pass
    referral.status = SpecialtyReferral.Status.ACCEPTED
    referral.accepted_at = timezone.now()
    referral.response_notes = response_notes
    referral.save(update_fields=['status', 'accepted_at', 'to_doctor', 'response_notes', 'updated_at'])
    log_action(request.user, AuditLog.Action.UPDATE, AuditLog.Module.VISIT,
               object_type='SpecialtyReferral', object_id=referral.pk,
               object_repr=str(referral), description=f'Referral #{referral.pk} accepted', request=request)
    messages.success(request, 'Referral accepted.')
    return redirect('referral_detail', pk=pk)


@hms_permission_required('core.accept_specialty_referral')
def referral_complete(request, pk):
    if request.method != 'POST':
        return redirect('referral_detail', pk=pk)
    referral = get_object_or_404(SpecialtyReferral, pk=pk)
    if referral.status not in (SpecialtyReferral.Status.ACCEPTED, SpecialtyReferral.Status.IN_PROGRESS):
        messages.error(request, 'Referral cannot be completed in its current state.')
        return redirect('referral_detail', pk=pk)
    referral.status = SpecialtyReferral.Status.COMPLETED
    referral.completed_at = timezone.now()
    referral.response_notes = request.POST.get('response_notes', referral.response_notes)
    referral.save(update_fields=['status', 'completed_at', 'response_notes', 'updated_at'])
    log_action(request.user, AuditLog.Action.UPDATE, AuditLog.Module.VISIT,
               object_type='SpecialtyReferral', object_id=referral.pk,
               object_repr=str(referral), description=f'Referral #{referral.pk} completed', request=request)
    messages.success(request, 'Referral marked as completed.')
    return redirect('referral_detail', pk=pk)


@hms_permission_required('core.accept_specialty_referral')
def referral_decline(request, pk):
    if request.method != 'POST':
        return redirect('referral_detail', pk=pk)
    referral = get_object_or_404(SpecialtyReferral, pk=pk, status=SpecialtyReferral.Status.PENDING)
    referral.status = SpecialtyReferral.Status.DECLINED
    referral.response_notes = request.POST.get('response_notes', '').strip()
    referral.save(update_fields=['status', 'response_notes', 'updated_at'])
    log_action(request.user, AuditLog.Action.UPDATE, AuditLog.Module.VISIT,
               object_type='SpecialtyReferral', object_id=referral.pk,
               object_repr=str(referral), description=f'Referral #{referral.pk} declined', request=request)
    messages.success(request, 'Referral declined.')
    return redirect('referral_detail', pk=pk)


# ── Specialty Reports ─────────────────────────────────────────────────────────

@hms_permission_required('core.view_specialty_reports')
def specialty_reports(request):
    from django.db.models import Sum
    from .models import Appointment, Invoice

    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today.replace(day=1)))
    date_to_str = request.GET.get('date_to', str(today))
    spec_id = request.GET.get('specialty', '')

    try:
        date_from = timezone.datetime.strptime(date_from_str, '%Y-%m-%d').date()
        date_to = timezone.datetime.strptime(date_to_str, '%Y-%m-%d').date()
    except ValueError:
        date_from = today.replace(day=1)
        date_to = today

    visit_qs = Visit.objects.filter(created_at__date__range=[date_from, date_to])
    appt_qs = Appointment.objects.filter(appointment_date__range=[date_from, date_to])
    ref_qs = SpecialtyReferral.objects.filter(created_at__date__range=[date_from, date_to])

    if spec_id:
        visit_qs = visit_qs.filter(specialty_id=spec_id)
        appt_qs = appt_qs.filter(specialty_id=spec_id)
        ref_qs = ref_qs.filter(Q(from_specialty_id=spec_id) | Q(to_specialty_id=spec_id))

    # Visits by specialty
    visits_by_specialty = list(
        visit_qs.values('specialty__name').annotate(count=Count('id'))
        .order_by('-count')
    )

    # Appointments by specialty
    appts_by_specialty = list(
        appt_qs.values('specialty__name').annotate(count=Count('id'))
        .order_by('-count')
    )

    # Referrals by target specialty
    referrals_by_target = list(
        ref_qs.values('to_specialty__name').annotate(count=Count('id'))
        .order_by('-count')
    )

    # Referral status breakdown
    referral_status = list(
        ref_qs.values('status').annotate(count=Count('id'))
    )

    # Doctor workload (visits per doctor)
    doctor_workload = list(
        visit_qs.select_related('doctor').values(
            'doctor__first_name', 'doctor__last_name', 'specialty__name'
        ).annotate(count=Count('id')).order_by('-count')[:20]
    )

    specializations = Specialization.objects.filter(is_active=True).order_by('name')

    # Summary KPIs
    total_visits = visit_qs.count()
    total_appts = appt_qs.count()
    total_referrals = ref_qs.count()
    pending_referrals = ref_qs.filter(status=SpecialtyReferral.Status.PENDING).count()

    return render(request, 'specialty_mgmt/reports.html', {
        'date_from': date_from_str,
        'date_to': date_to_str,
        'selected_spec': spec_id,
        'specializations': specializations,
        'visits_by_specialty': json.dumps(visits_by_specialty),
        'appts_by_specialty': json.dumps(appts_by_specialty),
        'referrals_by_target': json.dumps([
            {'label': r['to_specialty__name'] or 'Unassigned', 'count': r['count']}
            for r in referrals_by_target
        ]),
        'referral_status': json.dumps([
            {'label': r['status'], 'count': r['count']}
            for r in referral_status
        ]),
        'doctor_workload': doctor_workload,
        'kpis': [
            {'label': 'Total Visits', 'value': total_visits},
            {'label': 'Total Appointments', 'value': total_appts},
            {'label': 'Total Referrals', 'value': total_referrals},
            {'label': 'Pending Referrals', 'value': pending_referrals},
        ],
    })
