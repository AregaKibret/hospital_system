"""
Internal Medicine module views.
Clinics: General Medical, Hypertension, Diabetes, Cardiac, Endocrinology,
         Gastroenterology, Infectious Diseases, Respiratory, Nephrology,
         Rheumatology, Chronic Follow-up.
"""

from datetime import date

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import hms_permission_required
from .models import (
    ChronicDiseasePlan, ClinicalRiskAssessment,
    InternalMedicineConsultation, Patient,
)


def _patient_search(q):
    if not q:
        return Patient.objects.none()
    return Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(middle_name__icontains=q) | Q(card_number__icontains=q) |
        Q(mobile__icontains=q)
    )[:20]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_im_dashboard')
def im_dashboard(request):
    today = date.today()
    consultations = InternalMedicineConsultation.objects.select_related('patient', 'clinician')

    kpis = {
        'today':         consultations.filter(consultation_date=today).count(),
        'this_month':    consultations.filter(consultation_date__year=today.year, consultation_date__month=today.month).count(),
        'chronic_plans': ChronicDiseasePlan.objects.count(),
        'risk_assessments': ClinicalRiskAssessment.objects.count(),
    }

    clinic_counts = (
        consultations
        .values('clinic_type')
        .annotate(n=Count('id'))
        .order_by('-n')
    )
    clinic_label_map = dict(InternalMedicineConsultation.ClinicType.choices)
    clinic_data = [
        {'label': clinic_label_map.get(r['clinic_type'], r['clinic_type']), 'count': r['n']}
        for r in clinic_counts
    ]

    recent = consultations.order_by('-consultation_date', '-created_at')[:10]

    return render(request, 'internal_medicine/dashboard.html', {
        'kpis': kpis,
        'clinic_data': clinic_data,
        'recent': recent,
        'clinic_choices': InternalMedicineConsultation.ClinicType.choices,
    })


# ── Consultation CRUD ─────────────────────────────────────────────────────────

@hms_permission_required('core.view_im_consultation')
def im_consultation_list(request):
    qs = InternalMedicineConsultation.objects.select_related('patient', 'clinician').order_by('-consultation_date', '-created_at')

    q = request.GET.get('q', '').strip()
    clinic = request.GET.get('clinic', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if clinic:
        qs = qs.filter(clinic_type=clinic)
    if date_from:
        qs = qs.filter(consultation_date__gte=date_from)
    if date_to:
        qs = qs.filter(consultation_date__lte=date_to)

    return render(request, 'internal_medicine/consultation_list.html', {
        'consultations': qs[:200],
        'q': q, 'clinic': clinic, 'date_from': date_from, 'date_to': date_to,
        'clinic_choices': InternalMedicineConsultation.ClinicType.choices,
        'total': qs.count(),
    })


@hms_permission_required('core.view_im_consultation')
def im_consultation_detail(request, pk):
    consult = get_object_or_404(InternalMedicineConsultation.objects.select_related('patient', 'clinician', 'visit'), pk=pk)
    risk_assessments = consult.risk_assessments.all()
    chronic_plans = ChronicDiseasePlan.objects.filter(patient=consult.patient).order_by('disease')
    return render(request, 'internal_medicine/consultation_detail.html', {
        'consult': consult,
        'risk_assessments': risk_assessments,
        'chronic_plans': chronic_plans,
    })


@hms_permission_required('core.manage_im_consultation')
def im_consultation_create(request):
    patient = None
    patient_id = request.GET.get('patient_id') or request.POST.get('patient_id')
    if patient_id:
        patient = get_object_or_404(Patient, pk=patient_id)

    if request.method == 'POST':
        p = request.POST
        pid = p.get('patient_id')
        if not pid:
            messages.error(request, 'Please select a patient.')
            return render(request, 'internal_medicine/consultation_form.html', _im_form_context(p, patient))

        pat = get_object_or_404(Patient, pk=pid)
        consult = InternalMedicineConsultation(
            patient=pat,
            consultation_date=p.get('consultation_date') or date.today(),
            clinic_type=p.get('clinic_type', 'general'),
            visit_type=p.get('visit_type', 'new'),
            clinician=request.user if request.user.is_authenticated else None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            pulse=p.get('pulse') or None,
            temperature=p.get('temperature') or None,
            weight_kg=p.get('weight_kg') or None,
            height_cm=p.get('height_cm') or None,
            spo2=p.get('spo2') or None,
            rr=p.get('rr') or None,
            fbs=p.get('fbs') or None,
            rbs=p.get('rbs') or None,
            hba1c=p.get('hba1c') or None,
            chief_complaint=p.get('chief_complaint', ''),
            history_of_presenting=p.get('history_of_presenting', ''),
            past_medical_history=p.get('past_medical_history', ''),
            family_history=p.get('family_history', ''),
            social_history=p.get('social_history', ''),
            current_medications=p.get('current_medications', ''),
            allergies=p.get('allergies', ''),
            review_of_systems=p.get('review_of_systems', ''),
            physical_examination=p.get('physical_examination', ''),
            diagnosis=p.get('diagnosis', ''),
            icd10_code=p.get('icd10_code', ''),
            plan=p.get('plan', ''),
            investigations=p.get('investigations', ''),
            referral=p.get('referral', ''),
            follow_up_date=p.get('follow_up_date') or None,
            follow_up_notes=p.get('follow_up_notes', ''),
        )
        consult.save()
        messages.success(request, f'Consultation recorded for {pat}.')
        return redirect('im_consultation_detail', pk=consult.pk)

    return render(request, 'internal_medicine/consultation_form.html', _im_form_context({}, patient))


@hms_permission_required('core.manage_im_consultation')
def im_consultation_edit(request, pk):
    consult = get_object_or_404(InternalMedicineConsultation, pk=pk)
    if request.method == 'POST':
        p = request.POST
        consult.consultation_date = p.get('consultation_date') or consult.consultation_date
        consult.clinic_type = p.get('clinic_type', consult.clinic_type)
        consult.visit_type = p.get('visit_type', consult.visit_type)
        for field in ['bp_systolic','bp_diastolic','pulse','spo2','rr']:
            setattr(consult, field, p.get(field) or None)
        for field in ['temperature','weight_kg','height_cm','fbs','rbs','hba1c']:
            setattr(consult, field, p.get(field) or None)
        for field in ['chief_complaint','history_of_presenting','past_medical_history',
                      'family_history','social_history','current_medications','allergies',
                      'review_of_systems','physical_examination','diagnosis','icd10_code',
                      'plan','investigations','referral','follow_up_notes']:
            setattr(consult, field, p.get(field, ''))
        consult.follow_up_date = p.get('follow_up_date') or None
        consult.save()
        messages.success(request, 'Consultation updated.')
        return redirect('im_consultation_detail', pk=consult.pk)

    return render(request, 'internal_medicine/consultation_form.html', {
        **_im_form_context({}, consult.patient),
        'consult': consult,
        'action': 'Edit',
    })


def _im_form_context(post, patient):
    return {
        'patient': patient,
        'post': post,
        'action': 'New',
        'clinic_choices': InternalMedicineConsultation.ClinicType.choices,
        'visit_type_choices': InternalMedicineConsultation.VisitType.choices,
        'today': date.today().isoformat(),
    }


# ── Chronic Disease Plans ─────────────────────────────────────────────────────

@hms_permission_required('core.view_chronic_plan')
def chronic_plan_list(request):
    qs = ChronicDiseasePlan.objects.select_related('patient').order_by('-created_at')
    q = request.GET.get('q', '').strip()
    disease = request.GET.get('disease', '')
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if disease:
        qs = qs.filter(disease=disease)
    return render(request, 'internal_medicine/chronic_plan_list.html', {
        'plans': qs[:200], 'q': q, 'disease': disease,
        'disease_choices': ChronicDiseasePlan.Disease.choices,
        'total': qs.count(),
    })


@hms_permission_required('core.manage_chronic_plan')
def chronic_plan_create(request):
    patient = None
    pid = request.GET.get('patient_id') or request.POST.get('patient_id')
    if pid:
        patient = get_object_or_404(Patient, pk=pid)

    if request.method == 'POST':
        p = request.POST
        pid = p.get('patient_id')
        if not pid:
            messages.error(request, 'Please select a patient.')
            return render(request, 'internal_medicine/chronic_plan_form.html', _cdp_context(p, patient))
        pat = get_object_or_404(Patient, pk=pid)
        plan = ChronicDiseasePlan(
            patient=pat,
            disease=p.get('disease'),
            stage=p.get('stage', 'unspecified'),
            date_diagnosed=p.get('date_diagnosed') or None,
            treatment_goals=p.get('treatment_goals', ''),
            medications=p.get('medications', ''),
            lifestyle_mods=p.get('lifestyle_mods', ''),
            monitoring_frequency=p.get('monitoring_frequency', ''),
            target_bp=p.get('target_bp', ''),
            target_hba1c=p.get('target_hba1c', ''),
            target_glucose=p.get('target_glucose', ''),
            is_controlled=p.get('is_controlled') == 'on',
            last_review=p.get('last_review') or None,
            next_review=p.get('next_review') or None,
            notes=p.get('notes', ''),
            clinician=request.user if request.user.is_authenticated else None,
        )
        plan.save()
        messages.success(request, f'Chronic disease plan created for {pat}.')
        return redirect('chronic_plan_list')

    return render(request, 'internal_medicine/chronic_plan_form.html', _cdp_context({}, patient))


@hms_permission_required('core.manage_chronic_plan')
def chronic_plan_edit(request, pk):
    plan = get_object_or_404(ChronicDiseasePlan, pk=pk)
    if request.method == 'POST':
        p = request.POST
        plan.disease = p.get('disease', plan.disease)
        plan.stage = p.get('stage', plan.stage)
        plan.date_diagnosed = p.get('date_diagnosed') or None
        for field in ['treatment_goals','medications','lifestyle_mods','monitoring_frequency',
                      'target_bp','target_hba1c','target_glucose','notes']:
            setattr(plan, field, p.get(field, ''))
        plan.is_controlled = p.get('is_controlled') == 'on'
        plan.last_review = p.get('last_review') or None
        plan.next_review = p.get('next_review') or None
        plan.save()
        messages.success(request, 'Chronic disease plan updated.')
        return redirect('chronic_plan_list')

    return render(request, 'internal_medicine/chronic_plan_form.html', {
        **_cdp_context({}, plan.patient),
        'plan': plan, 'action': 'Edit',
    })


def _cdp_context(post, patient):
    return {
        'patient': patient, 'post': post, 'action': 'New',
        'disease_choices': ChronicDiseasePlan.Disease.choices,
        'stage_choices': ChronicDiseasePlan.Stage.choices,
        'today': date.today().isoformat(),
    }


# ── Patient search API ────────────────────────────────────────────────────────

@hms_permission_required('core.view_im_dashboard')
def im_patient_search(request):
    from django.http import JsonResponse
    q = request.GET.get('q', '').strip()
    results = _patient_search(q)
    data = [{'id': p.pk, 'name': str(p), 'card': p.card_number, 'dob': str(p.date_of_birth or '')} for p in results]
    return JsonResponse({'results': data})


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_im_reports')
def im_reports(request):
    today = date.today()
    date_from = request.GET.get('date_from') or date(today.year, today.month, 1).isoformat()
    date_to = request.GET.get('date_to') or today.isoformat()
    report_type = request.GET.get('report_type', 'summary')

    qs = InternalMedicineConsultation.objects.filter(
        consultation_date__gte=date_from, consultation_date__lte=date_to
    )

    by_clinic = list(qs.values('clinic_type').annotate(n=Count('id')).order_by('-n'))
    label_map = dict(InternalMedicineConsultation.ClinicType.choices)
    for r in by_clinic:
        r['label'] = label_map.get(r['clinic_type'], r['clinic_type'])

    by_visit_type = list(qs.values('visit_type').annotate(n=Count('id')).order_by('-n'))
    vt_map = dict(InternalMedicineConsultation.VisitType.choices)
    for r in by_visit_type:
        r['label'] = vt_map.get(r['visit_type'], r['visit_type'])

    chronic = ChronicDiseasePlan.objects.all()
    by_disease = list(chronic.values('disease').annotate(n=Count('id')).order_by('-n'))
    d_map = dict(ChronicDiseasePlan.Disease.choices)
    for r in by_disease:
        r['label'] = d_map.get(r['disease'], r['disease'])

    return render(request, 'internal_medicine/reports.html', {
        'date_from': date_from, 'date_to': date_to,
        'report_type': report_type,
        'total': qs.count(),
        'by_clinic': by_clinic,
        'by_visit_type': by_visit_type,
        'by_disease': by_disease,
        'consultations': qs.select_related('patient').order_by('-consultation_date')[:100] if report_type == 'detail' else [],
        'report_type_choices': [
            ('summary', 'Summary Report'),
            ('detail', 'Detailed Listing'),
            ('chronic', 'Chronic Disease Plans'),
        ],
    })
