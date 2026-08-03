"""
Pediatrics module views.
Covers: Pediatric Consultation, Growth Monitoring, Immunization Tracking,
        Developmental Assessment, Nutrition Assessment.
"""

from datetime import date

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import hms_permission_required
from .models import (
    DevelopmentalAssessment, GrowthRecord, ImmunizationRecord,
    Patient, PediatricConsultation,
)

VACCINE_SCHEDULE = [
    ('BCG', 1), ('Hepatitis B (Birth)', 1),
    ('OPV 0', 1), ('OPV 1', 1), ('OPV 2', 1), ('OPV 3', 1),
    ('DPT-HepB-Hib 1', 1), ('DPT-HepB-Hib 2', 1), ('DPT-HepB-Hib 3', 1),
    ('PCV 1', 1), ('PCV 2', 1), ('PCV 3', 1),
    ('Rota 1', 1), ('Rota 2', 1), ('Rota 3', 1),
    ('Measles-Rubella 1', 1), ('Measles-Rubella 2', 1),
    ('Yellow Fever', 1), ('Meningitis A', 1),
    ('HPV 1', 1), ('HPV 2', 1),
]


def _patient_search(q):
    if not q:
        return Patient.objects.none()
    return Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(card_number__icontains=q)
    )[:20]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_peds_dashboard')
def peds_dashboard(request):
    today = date.today()
    consults = PediatricConsultation.objects.select_related('patient')

    kpis = {
        'today':         consults.filter(consultation_date=today).count(),
        'this_month':    consults.filter(consultation_date__year=today.year, consultation_date__month=today.month).count(),
        'growth_records':GrowthRecord.objects.count(),
        'immunizations': ImmunizationRecord.objects.filter(status='given').count(),
        'overdue_vaccines': ImmunizationRecord.objects.filter(status='overdue').count(),
    }

    visit_counts = list(
        consults.values('visit_type').annotate(n=Count('id')).order_by('-n')
    )
    vt_map = dict(PediatricConsultation.VisitType.choices)
    for r in visit_counts:
        r['label'] = vt_map.get(r['visit_type'], r['visit_type'])

    recent = consults.order_by('-consultation_date', '-created_at')[:10]

    return render(request, 'pediatrics/dashboard.html', {
        'kpis': kpis,
        'visit_counts': visit_counts,
        'recent': recent,
    })


# ── Consultation CRUD ─────────────────────────────────────────────────────────

@hms_permission_required('core.view_peds_consultation')
def peds_consultation_list(request):
    qs = PediatricConsultation.objects.select_related('patient', 'clinician').order_by('-consultation_date', '-created_at')
    q = request.GET.get('q', '').strip()
    visit_type = request.GET.get('visit_type', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if visit_type:
        qs = qs.filter(visit_type=visit_type)
    if date_from:
        qs = qs.filter(consultation_date__gte=date_from)
    if date_to:
        qs = qs.filter(consultation_date__lte=date_to)

    return render(request, 'pediatrics/consultation_list.html', {
        'consultations': qs[:200],
        'q': q, 'visit_type': visit_type, 'date_from': date_from, 'date_to': date_to,
        'visit_type_choices': PediatricConsultation.VisitType.choices,
        'total': qs.count(),
    })


@hms_permission_required('core.view_peds_consultation')
def peds_consultation_detail(request, pk):
    consult = get_object_or_404(PediatricConsultation.objects.select_related('patient', 'clinician'), pk=pk)
    growth = GrowthRecord.objects.filter(patient=consult.patient).order_by('-record_date')[:10]
    vaccines = ImmunizationRecord.objects.filter(patient=consult.patient).order_by('due_date')
    dev_assessments = DevelopmentalAssessment.objects.filter(patient=consult.patient).order_by('-assessment_date')[:5]
    return render(request, 'pediatrics/consultation_detail.html', {
        'consult': consult, 'growth': growth,
        'vaccines': vaccines, 'dev_assessments': dev_assessments,
    })


@hms_permission_required('core.manage_peds_consultation')
def peds_consultation_create(request):
    patient = None
    pid = request.GET.get('patient_id') or request.POST.get('patient_id')
    if pid:
        patient = get_object_or_404(Patient, pk=pid)

    if request.method == 'POST':
        p = request.POST
        pid = p.get('patient_id')
        if not pid:
            messages.error(request, 'Please select a patient.')
            return render(request, 'pediatrics/consultation_form.html', _peds_form_ctx(p, patient))
        pat = get_object_or_404(Patient, pk=pid)

        def _dec(key):
            v = p.get(key)
            return v if v else None

        consult = PediatricConsultation(
            patient=pat,
            consultation_date=p.get('consultation_date') or date.today(),
            visit_type=p.get('visit_type', 'new'),
            clinician=request.user if request.user.is_authenticated else None,
            weight_kg=_dec('weight_kg'), height_cm=_dec('height_cm'),
            head_circumference=_dec('head_circumference'), muac_cm=_dec('muac_cm'),
            temperature=_dec('temperature'), pulse=_dec('pulse') or None,
            rr=_dec('rr') or None, spo2=_dec('spo2') or None,
            bp_systolic=_dec('bp_systolic') or None, bp_diastolic=_dec('bp_diastolic') or None,
            weight_for_age_z=_dec('weight_for_age_z'),
            height_for_age_z=_dec('height_for_age_z'),
            weight_for_height_z=_dec('weight_for_height_z'),
            chief_complaint=p.get('chief_complaint', ''),
            history=p.get('history', ''),
            birth_history=p.get('birth_history', ''),
            feeding_history=p.get('feeding_history', ''),
            immunization_history=p.get('immunization_history', ''),
            developmental_history=p.get('developmental_history', ''),
            family_history=p.get('family_history', ''),
            social_history=p.get('social_history', ''),
            general_appearance=p.get('general_appearance', ''),
            systems_examination=p.get('systems_examination', ''),
            diagnosis=p.get('diagnosis', ''),
            plan=p.get('plan', ''),
            counseling_given=p.get('counseling_given', ''),
            follow_up_date=_dec('follow_up_date'),
            referral=p.get('referral', ''),
        )
        consult.save()

        # Optionally log growth record
        if p.get('weight_kg') or p.get('height_cm'):
            GrowthRecord.objects.create(
                patient=pat,
                record_date=consult.consultation_date,
                weight_kg=consult.weight_kg,
                height_cm=consult.height_cm,
                head_circumference=consult.head_circumference,
                muac_cm=consult.muac_cm,
                weight_for_age_z=consult.weight_for_age_z,
                height_for_age_z=consult.height_for_age_z,
                weight_for_height_z=consult.weight_for_height_z,
                recorded_by=request.user if request.user.is_authenticated else None,
            )

        messages.success(request, f'Consultation recorded for {pat}.')
        return redirect('peds_consultation_detail', pk=consult.pk)

    return render(request, 'pediatrics/consultation_form.html', _peds_form_ctx({}, patient))


@hms_permission_required('core.manage_peds_consultation')
def peds_consultation_edit(request, pk):
    consult = get_object_or_404(PediatricConsultation, pk=pk)
    if request.method == 'POST':
        p = request.POST
        consult.consultation_date = p.get('consultation_date') or consult.consultation_date
        consult.visit_type = p.get('visit_type', consult.visit_type)
        for field in ['weight_kg','height_cm','head_circumference','muac_cm','temperature',
                      'weight_for_age_z','height_for_age_z','weight_for_height_z']:
            setattr(consult, field, p.get(field) or None)
        for field in ['pulse','rr','spo2','bp_systolic','bp_diastolic']:
            setattr(consult, field, p.get(field) or None)
        for field in ['chief_complaint','history','birth_history','feeding_history',
                      'immunization_history','developmental_history','family_history',
                      'social_history','general_appearance','systems_examination',
                      'diagnosis','plan','counseling_given','referral']:
            setattr(consult, field, p.get(field, ''))
        consult.follow_up_date = p.get('follow_up_date') or None
        consult.save()
        messages.success(request, 'Consultation updated.')
        return redirect('peds_consultation_detail', pk=consult.pk)

    return render(request, 'pediatrics/consultation_form.html', {
        **_peds_form_ctx({}, consult.patient),
        'consult': consult, 'action': 'Edit',
    })


def _peds_form_ctx(post, patient):
    return {
        'patient': patient, 'post': post, 'action': 'New',
        'visit_type_choices': PediatricConsultation.VisitType.choices,
        'today': date.today().isoformat(),
    }


# ── Growth Records ────────────────────────────────────────────────────────────

@hms_permission_required('core.view_growth_record')
def growth_record_list(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    records = GrowthRecord.objects.filter(patient=patient).order_by('-record_date')
    return render(request, 'pediatrics/growth_list.html', {'patient': patient, 'records': records})


@hms_permission_required('core.manage_growth_record')
def growth_record_create(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == 'POST':
        p = request.POST
        GrowthRecord.objects.create(
            patient=patient,
            record_date=p.get('record_date') or date.today(),
            age_months=p.get('age_months') or None,
            weight_kg=p.get('weight_kg') or None,
            height_cm=p.get('height_cm') or None,
            head_circumference=p.get('head_circumference') or None,
            muac_cm=p.get('muac_cm') or None,
            weight_for_age_z=p.get('weight_for_age_z') or None,
            height_for_age_z=p.get('height_for_age_z') or None,
            weight_for_height_z=p.get('weight_for_height_z') or None,
            notes=p.get('notes', ''),
            recorded_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, 'Growth record saved.')
        return redirect('growth_record_list', patient_id=patient.pk)
    return render(request, 'pediatrics/growth_form.html', {
        'patient': patient, 'today': date.today().isoformat(),
    })


# ── Immunizations ─────────────────────────────────────────────────────────────

@hms_permission_required('core.view_immunization')
def immunization_list(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    vaccines = ImmunizationRecord.objects.filter(patient=patient).order_by('due_date', 'vaccine_name')
    overdue = vaccines.filter(status='overdue').count()
    return render(request, 'pediatrics/immunization_list.html', {
        'patient': patient, 'vaccines': vaccines, 'overdue': overdue,
        'status_choices': ImmunizationRecord.Status.choices,
    })


@hms_permission_required('core.manage_immunization')
def immunization_record(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == 'POST':
        p = request.POST
        ImmunizationRecord.objects.create(
            patient=patient,
            vaccine_name=p.get('vaccine_name', ''),
            dose_number=p.get('dose_number', 1),
            due_date=p.get('due_date') or None,
            date_given=p.get('date_given') or None,
            status=p.get('status', 'given'),
            batch_number=p.get('batch_number', ''),
            site=p.get('site', ''),
            administered_by=request.user if p.get('status') == 'given' and request.user.is_authenticated else None,
            reaction=p.get('reaction', ''),
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Immunization record saved.')
        return redirect('immunization_list', patient_id=patient.pk)
    return render(request, 'pediatrics/immunization_form.html', {
        'patient': patient,
        'vaccine_schedule': VACCINE_SCHEDULE,
        'status_choices': ImmunizationRecord.Status.choices,
        'today': date.today().isoformat(),
    })


# ── Developmental Assessment ──────────────────────────────────────────────────

@hms_permission_required('core.manage_dev_assessment')
def dev_assessment_create(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == 'POST':
        p = request.POST
        DevelopmentalAssessment.objects.create(
            patient=patient,
            assessment_date=p.get('assessment_date') or date.today(),
            age_months=p.get('age_months', 0),
            clinician=request.user if request.user.is_authenticated else None,
            gross_motor=p.get('gross_motor', ''),
            fine_motor=p.get('fine_motor', ''),
            language=p.get('language', ''),
            social_personal=p.get('social_personal', ''),
            cognitive=p.get('cognitive', ''),
            milestones_met=p.get('milestones_met', ''),
            milestones_missed=p.get('milestones_missed', ''),
            outcome=p.get('outcome', 'normal'),
            recommendations=p.get('recommendations', ''),
            next_assessment=p.get('next_assessment') or None,
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Developmental assessment saved.')
        return redirect('peds_consultation_list')
    return render(request, 'pediatrics/dev_assessment_form.html', {
        'patient': patient,
        'outcome_choices': DevelopmentalAssessment.Outcome.choices,
        'today': date.today().isoformat(),
    })


# ── Patient search API ────────────────────────────────────────────────────────

@hms_permission_required('core.view_peds_dashboard')
def peds_patient_search(request):
    from django.http import JsonResponse
    q = request.GET.get('q', '').strip()
    results = _patient_search(q)
    data = [{'id': p.pk, 'name': str(p), 'card': p.card_number, 'dob': str(p.date_of_birth or '')} for p in results]
    return JsonResponse({'results': data})


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_peds_reports')
def peds_reports(request):
    today = date.today()
    date_from = request.GET.get('date_from') or date(today.year, today.month, 1).isoformat()
    date_to = request.GET.get('date_to') or today.isoformat()
    report_type = request.GET.get('report_type', 'summary')

    qs = PediatricConsultation.objects.filter(
        consultation_date__gte=date_from, consultation_date__lte=date_to
    )

    by_visit = list(qs.values('visit_type').annotate(n=Count('id')).order_by('-n'))
    vt_map = dict(PediatricConsultation.VisitType.choices)
    for r in by_visit:
        r['label'] = vt_map.get(r['visit_type'], r['visit_type'])

    vaccine_stats = ImmunizationRecord.objects.values('status').annotate(n=Count('id'))
    st_map = dict(ImmunizationRecord.Status.choices)
    vaccine_data = [{'label': st_map.get(r['status'], r['status']), 'count': r['n']} for r in vaccine_stats]

    nutrition_data = []
    for label, code in [('SAM','sam'),('MAM','mam'),('Normal','normal'),('Overweight','overweight')]:
        if code == 'sam':
            n = PediatricConsultation.objects.filter(weight_for_height_z__lt=-3).count()
        elif code == 'mam':
            n = PediatricConsultation.objects.filter(weight_for_height_z__gte=-3, weight_for_height_z__lt=-2).count()
        elif code == 'normal':
            n = PediatricConsultation.objects.filter(weight_for_height_z__gte=-2, weight_for_height_z__lt=2).count()
        else:
            n = PediatricConsultation.objects.filter(weight_for_height_z__gte=2).count()
        nutrition_data.append({'label': label, 'count': n})

    return render(request, 'pediatrics/reports.html', {
        'date_from': date_from, 'date_to': date_to,
        'report_type': report_type,
        'total': qs.count(),
        'by_visit': by_visit,
        'vaccine_data': vaccine_data,
        'nutrition_data': nutrition_data,
        'consultations': qs.select_related('patient').order_by('-consultation_date')[:100] if report_type == 'detail' else [],
        'report_type_choices': [
            ('summary', 'Summary Report'),
            ('detail', 'Detailed Listing'),
            ('immunization', 'Immunization Coverage'),
        ],
    })
