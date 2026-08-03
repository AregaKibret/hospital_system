"""
General Surgery module views.
Covers: Surgical Consultation, Pre-operative Assessment, Operative Note,
        Post-operative Notes, Wound Follow-up.
"""

from datetime import date

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import hms_permission_required
from .models import (
    SurgicalOperativeNote, Patient, SurgicalPostOpNote,
    PreOperativeAssessment, SurgicalConsultation, WoundFollowUp,
)


def _patient_search(q):
    if not q:
        return Patient.objects.none()
    return Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(card_number__icontains=q)
    )[:20]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_surgery_consult_dashboard')
def surgery_consult_dashboard(request):
    today = date.today()
    consults = SurgicalConsultation.objects.select_related('patient', 'surgeon')

    kpis = {
        'today':          consults.filter(consultation_date=today).count(),
        'this_month':     consults.filter(consultation_date__year=today.year, consultation_date__month=today.month).count(),
        'pending_surgery':consults.filter(decision='for_surgery').count(),
        'operations':     SurgicalOperativeNote.objects.count(),
        'wound_followups':WoundFollowUp.objects.count(),
    }

    urgency_counts = list(consults.values('urgency').annotate(n=Count('id')).order_by('urgency'))
    urg_map = dict(SurgicalConsultation.Urgency.choices)
    for r in urgency_counts:
        r['label'] = urg_map.get(r['urgency'], r['urgency'])

    decision_counts = list(consults.values('decision').annotate(n=Count('id')).order_by('-n'))
    dec_map = dict(SurgicalConsultation.Decision.choices)
    for r in decision_counts:
        r['label'] = dec_map.get(r['decision'], r['decision'])

    recent = consults.order_by('-consultation_date', '-created_at')[:10]
    recent_ops = SurgicalOperativeNote.objects.select_related('consultation__patient').order_by('-procedure_date')[:5]

    return render(request, 'general_surgery/dashboard.html', {
        'kpis': kpis,
        'urgency_counts': urgency_counts,
        'decision_counts': decision_counts,
        'recent': recent,
        'recent_ops': recent_ops,
    })


# ── Surgical Consultation CRUD ────────────────────────────────────────────────

@hms_permission_required('core.view_surgical_consultation')
def surgical_consultation_list(request):
    qs = SurgicalConsultation.objects.select_related('patient', 'surgeon').order_by('-consultation_date', '-created_at')
    q = request.GET.get('q', '').strip()
    urgency = request.GET.get('urgency', '')
    decision = request.GET.get('decision', '')

    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q) | Q(proposed_procedure__icontains=q)
        )
    if urgency:
        qs = qs.filter(urgency=urgency)
    if decision:
        qs = qs.filter(decision=decision)

    return render(request, 'general_surgery/consultation_list.html', {
        'consultations': qs[:200], 'q': q, 'urgency': urgency, 'decision': decision,
        'urgency_choices': SurgicalConsultation.Urgency.choices,
        'decision_choices': SurgicalConsultation.Decision.choices,
        'total': qs.count(),
    })


@hms_permission_required('core.view_surgical_consultation')
def surgical_consultation_detail(request, pk):
    consult = get_object_or_404(
        SurgicalConsultation.objects.select_related('patient', 'surgeon', 'visit'),
        pk=pk
    )
    preop = getattr(consult, 'preop_assessment', None)
    op_note = getattr(consult, 'operative_note', None)
    postop_notes = consult.postop_notes.order_by('pod')
    wound_followups = consult.wound_followups.order_by('visit_date')
    return render(request, 'general_surgery/consultation_detail.html', {
        'consult': consult, 'preop': preop,
        'op_note': op_note, 'postop_notes': postop_notes,
        'wound_followups': wound_followups,
    })


@hms_permission_required('core.manage_surgical_consultation')
def surgical_consultation_create(request):
    patient = None
    pid = request.GET.get('patient_id') or request.POST.get('patient_id')
    if pid:
        patient = get_object_or_404(Patient, pk=pid)

    if request.method == 'POST':
        p = request.POST
        pid = p.get('patient_id')
        if not pid:
            messages.error(request, 'Please select a patient.')
            return render(request, 'general_surgery/consultation_form.html', _surg_ctx(p, patient))
        pat = get_object_or_404(Patient, pk=pid)

        consult = SurgicalConsultation(
            patient=pat,
            consultation_date=p.get('consultation_date') or date.today(),
            surgeon=request.user if request.user.is_authenticated else None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            pulse=p.get('pulse') or None,
            temperature=p.get('temperature') or None,
            weight_kg=p.get('weight_kg') or None,
            spo2=p.get('spo2') or None,
            chief_complaint=p.get('chief_complaint', ''),
            history=p.get('history', ''),
            past_surgical_history=p.get('past_surgical_history', ''),
            past_medical_history=p.get('past_medical_history', ''),
            medications=p.get('medications', ''),
            allergies=p.get('allergies', ''),
            physical_examination=p.get('physical_examination', ''),
            local_examination=p.get('local_examination', ''),
            provisional_diagnosis=p.get('provisional_diagnosis', ''),
            proposed_procedure=p.get('proposed_procedure', ''),
            urgency=p.get('urgency', 'elective'),
            decision=p.get('decision', 'for_surgery'),
            pre_op_investigations=p.get('pre_op_investigations', ''),
            consent_obtained=p.get('consent_obtained') == 'on',
            anaesthesia_referral=p.get('anaesthesia_referral') == 'on',
            blood_required=p.get('blood_required') == 'on',
            units_required=p.get('units_required') or None,
            notes=p.get('notes', ''),
            follow_up_date=p.get('follow_up_date') or None,
        )
        consult.save()
        messages.success(request, f'Surgical consultation recorded for {pat}.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/consultation_form.html', _surg_ctx({}, patient))


@hms_permission_required('core.manage_surgical_consultation')
def surgical_consultation_edit(request, pk):
    consult = get_object_or_404(SurgicalConsultation, pk=pk)
    if request.method == 'POST':
        p = request.POST
        consult.consultation_date = p.get('consultation_date') or consult.consultation_date
        for field in ['bp_systolic','bp_diastolic','pulse','spo2','units_required']:
            setattr(consult, field, p.get(field) or None)
        for field in ['temperature','weight_kg']:
            setattr(consult, field, p.get(field) or None)
        for field in ['chief_complaint','history','past_surgical_history','past_medical_history',
                      'medications','allergies','physical_examination','local_examination',
                      'provisional_diagnosis','proposed_procedure','pre_op_investigations','notes']:
            setattr(consult, field, p.get(field, ''))
        consult.urgency = p.get('urgency', consult.urgency)
        consult.decision = p.get('decision', consult.decision)
        consult.consent_obtained = p.get('consent_obtained') == 'on'
        consult.anaesthesia_referral = p.get('anaesthesia_referral') == 'on'
        consult.blood_required = p.get('blood_required') == 'on'
        consult.follow_up_date = p.get('follow_up_date') or None
        consult.save()
        messages.success(request, 'Surgical consultation updated.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/consultation_form.html', {
        **_surg_ctx({}, consult.patient),
        'consult': consult, 'action': 'Edit',
    })


def _surg_ctx(post, patient):
    return {
        'patient': patient, 'post': post, 'action': 'New',
        'urgency_choices': SurgicalConsultation.Urgency.choices,
        'decision_choices': SurgicalConsultation.Decision.choices,
        'today': date.today().isoformat(),
    }


# ── Pre-operative Assessment ──────────────────────────────────────────────────

@hms_permission_required('core.manage_preop_assessment')
def preop_assessment_create(request, consultation_pk):
    consult = get_object_or_404(SurgicalConsultation, pk=consultation_pk)
    if hasattr(consult, 'preop_assessment'):
        return redirect('preop_assessment_edit', pk=consult.preop_assessment.pk)

    if request.method == 'POST':
        p = request.POST
        bools = ['hb_done','xmatch_done','ecg_done','cxr_done','lft_done','rft_done',
                 'clotting_done','urinalysis_done','consent_signed','npo_confirmed',
                 'site_marked','iv_access','pre_med_given','antibiotic_given','dvt_prophylaxis']
        kwargs = {b: p.get(b) == 'on' for b in bools}
        PreOperativeAssessment.objects.create(
            consultation=consult,
            assessment_date=p.get('assessment_date') or date.today(),
            assessed_by=request.user if request.user.is_authenticated else None,
            asa_class=p.get('asa_class', ''),
            airway_notes=p.get('airway_notes', ''),
            anaesthesia_plan=p.get('anaesthesia_plan', ''),
            special_concerns=p.get('special_concerns', ''),
            notes=p.get('notes', ''),
            **kwargs,
        )
        messages.success(request, 'Pre-operative assessment saved.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/preop_form.html', {
        'consult': consult,
        'asa_choices': PreOperativeAssessment.ASA.choices,
        'today': date.today().isoformat(),
    })


@hms_permission_required('core.manage_preop_assessment')
def preop_assessment_edit(request, pk):
    preop = get_object_or_404(PreOperativeAssessment, pk=pk)
    consult = preop.consultation
    if request.method == 'POST':
        p = request.POST
        bools = ['hb_done','xmatch_done','ecg_done','cxr_done','lft_done','rft_done',
                 'clotting_done','urinalysis_done','consent_signed','npo_confirmed',
                 'site_marked','iv_access','pre_med_given','antibiotic_given','dvt_prophylaxis']
        for b in bools:
            setattr(preop, b, p.get(b) == 'on')
        preop.asa_class = p.get('asa_class', preop.asa_class)
        for field in ['airway_notes','anaesthesia_plan','special_concerns','notes']:
            setattr(preop, field, p.get(field, ''))
        preop.assessment_date = p.get('assessment_date') or preop.assessment_date
        preop.save()
        messages.success(request, 'Pre-op assessment updated.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/preop_form.html', {
        'consult': consult, 'preop': preop,
        'asa_choices': PreOperativeAssessment.ASA.choices,
        'today': date.today().isoformat(),
    })


# ── Operative Note ────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_operative_note')
def operative_note_create_consult(request, consultation_pk):
    consult = get_object_or_404(SurgicalConsultation, pk=consultation_pk)
    if hasattr(consult, 'operative_note'):
        return redirect('operative_note_edit_consult', pk=consult.operative_note.pk)

    if request.method == 'POST':
        p = request.POST
        SurgicalOperativeNote.objects.create(
            consultation=consult,
            procedure_date=p.get('procedure_date') or date.today(),
            start_time=p.get('start_time') or None,
            end_time=p.get('end_time') or None,
            surgeon=request.user if request.user.is_authenticated else None,
            assistant_surgeon=p.get('assistant_surgeon', ''),
            anaesthetist=p.get('anaesthetist', ''),
            scrub_nurse=p.get('scrub_nurse', ''),
            procedure_performed=p.get('procedure_performed', ''),
            anaesthesia_type=p.get('anaesthesia_type', ''),
            position=p.get('position', ''),
            incision=p.get('incision', ''),
            findings=p.get('findings', ''),
            procedure_details=p.get('procedure_details', ''),
            closure=p.get('closure', ''),
            wound_class=p.get('wound_class', ''),
            ebl=p.get('ebl') or None,
            specimens_sent=p.get('specimens_sent', ''),
            drains_placed=p.get('drains_placed', ''),
            complications=p.get('complications', ''),
            post_op_orders=p.get('post_op_orders', ''),
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Operative note saved.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/operative_note_form.html', {
        'consult': consult,
        'wound_class_choices': SurgicalOperativeNote.WoundClass.choices,
        'today': date.today().isoformat(),
    })


@hms_permission_required('core.manage_operative_note')
def operative_note_edit_consult(request, pk):
    op = get_object_or_404(SurgicalOperativeNote, pk=pk)
    consult = op.consultation
    if request.method == 'POST':
        p = request.POST
        op.procedure_date = p.get('procedure_date') or op.procedure_date
        op.start_time = p.get('start_time') or None
        op.end_time = p.get('end_time') or None
        op.wound_class = p.get('wound_class', op.wound_class)
        op.ebl = p.get('ebl') or None
        for field in ['assistant_surgeon','anaesthetist','scrub_nurse','procedure_performed',
                      'anaesthesia_type','position','incision','findings','procedure_details',
                      'closure','specimens_sent','drains_placed','complications','post_op_orders','notes']:
            setattr(op, field, p.get(field, ''))
        op.save()
        messages.success(request, 'Operative note updated.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/operative_note_form.html', {
        'consult': consult, 'op': op,
        'wound_class_choices': SurgicalOperativeNote.WoundClass.choices,
        'today': date.today().isoformat(),
    })


# ── Post-operative Notes ──────────────────────────────────────────────────────

@hms_permission_required('core.manage_postop_note')
def postop_note_create_consult(request, consultation_pk):
    consult = get_object_or_404(SurgicalConsultation, pk=consultation_pk)
    last_pod = consult.postop_notes.order_by('-pod').values_list('pod', flat=True).first() or 0

    if request.method == 'POST':
        p = request.POST
        SurgicalPostOpNote.objects.create(
            consultation=consult,
            note_date=p.get('note_date') or date.today(),
            pod=p.get('pod', last_pod + 1),
            written_by=request.user if request.user.is_authenticated else None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            pulse=p.get('pulse') or None,
            temperature=p.get('temperature') or None,
            spo2=p.get('spo2') or None,
            pain_score=p.get('pain_score') or None,
            oral_intake=p.get('oral_intake', ''),
            urine_output=p.get('urine_output', ''),
            drain_output=p.get('drain_output', ''),
            wound_condition=p.get('wound_condition', ''),
            subjective=p.get('subjective', ''),
            objective=p.get('objective', ''),
            assessment=p.get('assessment', ''),
            plan=p.get('plan', ''),
            status=p.get('status', 'stable'),
            discharge_planned=p.get('discharge_planned') == 'on',
        )
        messages.success(request, f'Post-op note (Day {last_pod + 1}) saved.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/postop_note_form.html', {
        'consult': consult,
        'next_pod': last_pod + 1,
        'status_choices': SurgicalPostOpNote.Status.choices,
        'today': date.today().isoformat(),
    })


# ── Wound Follow-up ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_wound_followup')
def wound_followup_create(request, consultation_pk):
    consult = get_object_or_404(SurgicalConsultation, pk=consultation_pk)
    if request.method == 'POST':
        p = request.POST
        WoundFollowUp.objects.create(
            consultation=consult,
            visit_date=p.get('visit_date') or date.today(),
            pod=p.get('pod') or None,
            seen_by=request.user if request.user.is_authenticated else None,
            wound_status=p.get('wound_status', 'healing'),
            wound_findings=p.get('wound_findings', ''),
            sutures_removed=p.get('sutures_removed') == 'on',
            dressing_done=p.get('dressing_done') == 'on',
            dressing_type=p.get('dressing_type', ''),
            plan=p.get('plan', ''),
            next_visit=p.get('next_visit') or None,
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Wound follow-up recorded.')
        return redirect('surgical_consultation_detail', pk=consult.pk)

    return render(request, 'general_surgery/wound_followup_form.html', {
        'consult': consult,
        'status_choices': WoundFollowUp.WoundStatus.choices,
        'today': date.today().isoformat(),
    })


# ── Patient search API ────────────────────────────────────────────────────────

@hms_permission_required('core.view_surgery_consult_dashboard')
def surgery_consult_patient_search(request):
    from django.http import JsonResponse
    q = request.GET.get('q', '').strip()
    results = _patient_search(q)
    data = [{'id': p.pk, 'name': str(p), 'card': p.card_number, 'dob': str(p.date_of_birth or '')} for p in results]
    return JsonResponse({'results': data})


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_surgery_reports')
def surgery_consult_reports(request):
    today = date.today()
    date_from = request.GET.get('date_from') or date(today.year, today.month, 1).isoformat()
    date_to = request.GET.get('date_to') or today.isoformat()
    report_type = request.GET.get('report_type', 'summary')

    qs = SurgicalConsultation.objects.filter(
        consultation_date__gte=date_from, consultation_date__lte=date_to
    )

    by_urgency = list(qs.values('urgency').annotate(n=Count('id')).order_by('urgency'))
    urg_map = dict(SurgicalConsultation.Urgency.choices)
    for r in by_urgency:
        r['label'] = urg_map.get(r['urgency'], r['urgency'])

    by_decision = list(qs.values('decision').annotate(n=Count('id')).order_by('-n'))
    dec_map = dict(SurgicalConsultation.Decision.choices)
    for r in by_decision:
        r['label'] = dec_map.get(r['decision'], r['decision'])

    ops = SurgicalOperativeNote.objects.filter(procedure_date__gte=date_from, procedure_date__lte=date_to)
    by_wound = list(ops.values('wound_class').annotate(n=Count('id')))
    wc_map = dict(SurgicalOperativeNote.WoundClass.choices)
    for r in by_wound:
        r['label'] = wc_map.get(r['wound_class'], r['wound_class'])

    return render(request, 'general_surgery/reports.html', {
        'date_from': date_from, 'date_to': date_to,
        'report_type': report_type,
        'total': qs.count(),
        'total_ops': ops.count(),
        'by_urgency': by_urgency,
        'by_decision': by_decision,
        'by_wound': by_wound,
        'consultations': qs.select_related('patient').order_by('-consultation_date')[:100] if report_type == 'detail' else [],
        'report_type_choices': [
            ('summary', 'Summary Report'),
            ('detail', 'Detailed Listing'),
            ('operations', 'Operative Notes'),
        ],
    })
