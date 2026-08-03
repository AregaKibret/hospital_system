"""
Cardiology module views.
Covers: Consultations, ECG Records, Echocardiogram Reports, Cardiac Procedures.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import hms_permission_required
from .models import (
    CardiacProcedure, CardiologyConsultation, ECGRecord, EchoReport, Patient,
)


def _patient_search(q):
    qs = Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(mrn__icontains=q)
    )[:10]
    return [{'id': p.pk, 'name': str(p), 'mrn': p.mrn or ''} for p in qs]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_cardiology_dashboard')
def cardiology_dashboard(request):
    today = date.today()
    month_start = today.replace(day=1)

    consults = CardiologyConsultation.objects.all()
    kpis = {
        'today':       consults.filter(consultation_date=today).count(),
        'this_month':  consults.filter(consultation_date__gte=month_start).count(),
        'ecg_month':   ECGRecord.objects.filter(recording_date__gte=month_start).count(),
        'echo_month':  EchoReport.objects.filter(study_date__gte=month_start).count(),
        'procedures':  CardiacProcedure.objects.filter(procedure_date__gte=month_start).count(),
    }

    recent = consults.select_related('patient').order_by('-consultation_date', '-created_at')[:8]

    by_clinic = list(
        consults.filter(consultation_date__gte=month_start)
        .values('clinic_type').annotate(n=Count('id')).order_by('-n')
    )
    clinic_map = dict(CardiologyConsultation.ClinicType.choices)
    for r in by_clinic:
        r['label'] = clinic_map.get(r['clinic_type'], r['clinic_type'])

    by_nyha = list(
        consults.filter(consultation_date__gte=month_start)
        .exclude(nyha_class='na')
        .values('nyha_class').annotate(n=Count('id')).order_by('nyha_class')
    )
    nyha_map = dict(CardiologyConsultation.NYHAClass.choices)
    for r in by_nyha:
        r['label'] = nyha_map.get(r['nyha_class'], r['nyha_class'])

    return render(request, 'cardiology/dashboard.html', {
        'kpis': kpis, 'recent': recent,
        'by_clinic': by_clinic, 'by_nyha': by_nyha,
    })


# ── Consultations ─────────────────────────────────────────────────────────────

@hms_permission_required('core.view_cardiology_consultation')
def cardiology_consultation_list(request):
    qs = CardiologyConsultation.objects.select_related('patient').order_by('-consultation_date', '-created_at')
    q = request.GET.get('q', '').strip()
    clinic = request.GET.get('clinic', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(Q(patient__first_name__icontains=q) | Q(patient__last_name__icontains=q) | Q(patient__mrn__icontains=q))
    if clinic:
        qs = qs.filter(clinic_type=clinic)
    if date_from:
        qs = qs.filter(consultation_date__gte=date_from)
    if date_to:
        qs = qs.filter(consultation_date__lte=date_to)

    return render(request, 'cardiology/consultation_list.html', {
        'consultations': qs[:200],
        'q': q, 'clinic': clinic, 'date_from': date_from, 'date_to': date_to,
        'clinic_choices': CardiologyConsultation.ClinicType.choices,
    })


@hms_permission_required('core.view_cardiology_consultation')
def cardiology_consultation_detail(request, pk):
    consult = get_object_or_404(CardiologyConsultation.objects.select_related('patient', 'cardiologist'), pk=pk)
    ecg_records = consult.ecg_records.order_by('-recording_date')
    echo_reports = consult.echo_reports.order_by('-study_date')
    procedures = consult.cardiac_procedures.order_by('-procedure_date')
    return render(request, 'cardiology/consultation_detail.html', {
        'consultation': consult,
        'ecg_records': ecg_records,
        'echo_reports': echo_reports,
        'procedures': procedures,
    })


@hms_permission_required('core.manage_cardiology_consultation')
def cardiology_consultation_create(request):
    if request.method == 'POST':
        p = request.POST
        patient_id = p.get('patient')
        if not patient_id:
            messages.error(request, 'Please select a patient.')
            return render(request, 'cardiology/consultation_form.html', {
                'clinic_choices': CardiologyConsultation.ClinicType.choices,
                'visit_choices': CardiologyConsultation.VisitType.choices,
                'nyha_choices': CardiologyConsultation.NYHAClass.choices,
                'today': date.today().isoformat(),
            })
        patient = get_object_or_404(Patient, pk=patient_id)
        consult = CardiologyConsultation.objects.create(
            patient=patient,
            consultation_date=p.get('consultation_date') or date.today(),
            clinic_type=p.get('clinic_type', 'general'),
            visit_type=p.get('visit_type', 'new'),
            cardiologist=request.user if request.user.is_authenticated else None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            pulse=p.get('pulse') or None,
            temperature=p.get('temperature') or None,
            weight_kg=p.get('weight_kg') or None,
            height_cm=p.get('height_cm') or None,
            spo2=p.get('spo2') or None,
            rr=p.get('rr') or None,
            nyha_class=p.get('nyha_class', 'na'),
            jvp=p.get('jvp', ''),
            heart_sounds=p.get('heart_sounds', ''),
            murmur=p.get('murmur', ''),
            peripheral_pulses=p.get('peripheral_pulses', ''),
            pedal_edema=p.get('pedal_edema', ''),
            chest_pain=p.get('chest_pain') == 'on',
            dyspnea=p.get('dyspnea') == 'on',
            orthopnea=p.get('orthopnea') == 'on',
            pnd=p.get('pnd') == 'on',
            palpitations=p.get('palpitations') == 'on',
            syncope=p.get('syncope') == 'on',
            presyncope=p.get('presyncope') == 'on',
            fatigue=p.get('fatigue') == 'on',
            ankle_swelling=p.get('ankle_swelling') == 'on',
            chief_complaint=p.get('chief_complaint', ''),
            history=p.get('history', ''),
            past_cardiac_history=p.get('past_cardiac_history', ''),
            past_medical_history=p.get('past_medical_history', ''),
            family_history=p.get('family_history', ''),
            social_history=p.get('social_history', ''),
            current_medications=p.get('current_medications', ''),
            allergies=p.get('allergies', ''),
            hypertension=p.get('hypertension') == 'on',
            diabetes=p.get('diabetes') == 'on',
            dyslipidaemia=p.get('dyslipidaemia') == 'on',
            smoking=p.get('smoking') == 'on',
            obesity=p.get('obesity') == 'on',
            family_hx_cad=p.get('family_hx_cad') == 'on',
            ckd=p.get('ckd') == 'on',
            physical_examination=p.get('physical_examination', ''),
            ecg_findings=p.get('ecg_findings', ''),
            investigations=p.get('investigations', ''),
            diagnosis=p.get('diagnosis', ''),
            icd10_code=p.get('icd10_code', ''),
            plan=p.get('plan', ''),
            referral=p.get('referral', ''),
            follow_up_date=p.get('follow_up_date') or None,
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Consultation saved.')
        return redirect('cardiology_consultation_detail', pk=consult.pk)

    return render(request, 'cardiology/consultation_form.html', {
        'clinic_choices': CardiologyConsultation.ClinicType.choices,
        'visit_choices': CardiologyConsultation.VisitType.choices,
        'nyha_choices': CardiologyConsultation.NYHAClass.choices,
        'today': date.today().isoformat(),
    })


@hms_permission_required('core.manage_cardiology_consultation')
def cardiology_consultation_edit(request, pk):
    consult = get_object_or_404(CardiologyConsultation, pk=pk)
    if request.method == 'POST':
        p = request.POST
        fields = [
            'consultation_date', 'clinic_type', 'visit_type', 'nyha_class',
            'jvp', 'heart_sounds', 'murmur', 'peripheral_pulses', 'pedal_edema',
            'chief_complaint', 'history', 'past_cardiac_history', 'past_medical_history',
            'family_history', 'social_history', 'current_medications', 'allergies',
            'physical_examination', 'ecg_findings', 'investigations',
            'diagnosis', 'icd10_code', 'plan', 'referral', 'notes',
        ]
        for f in fields:
            setattr(consult, f, p.get(f, getattr(consult, f)))
        for num_f in ['bp_systolic', 'bp_diastolic', 'pulse', 'spo2', 'rr']:
            setattr(consult, num_f, p.get(num_f) or None)
        for dec_f in ['temperature', 'weight_kg', 'height_cm']:
            setattr(consult, dec_f, p.get(dec_f) or None)
        for bool_f in ['chest_pain', 'dyspnea', 'orthopnea', 'pnd', 'palpitations',
                        'syncope', 'presyncope', 'fatigue', 'ankle_swelling',
                        'hypertension', 'diabetes', 'dyslipidaemia', 'smoking',
                        'obesity', 'family_hx_cad', 'ckd']:
            setattr(consult, bool_f, p.get(bool_f) == 'on')
        consult.follow_up_date = p.get('follow_up_date') or None
        consult.save()
        messages.success(request, 'Consultation updated.')
        return redirect('cardiology_consultation_detail', pk=consult.pk)

    return render(request, 'cardiology/consultation_form.html', {
        'consultation': consult,
        'clinic_choices': CardiologyConsultation.ClinicType.choices,
        'visit_choices': CardiologyConsultation.VisitType.choices,
        'nyha_choices': CardiologyConsultation.NYHAClass.choices,
        'today': date.today().isoformat(),
    })


# ── ECG Records ───────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_ecg_record')
def ecg_record_create(request, consultation_pk):
    consult = get_object_or_404(CardiologyConsultation, pk=consultation_pk)
    if request.method == 'POST':
        p = request.POST
        ECGRecord.objects.create(
            consultation=consult,
            patient=consult.patient,
            recording_date=p.get('recording_date') or date.today(),
            recording_time=p.get('recording_time') or None,
            recorded_by=request.user if request.user.is_authenticated else None,
            heart_rate=p.get('heart_rate') or None,
            rhythm=p.get('rhythm', 'sinus_normal'),
            axis=p.get('axis', 'normal'),
            pr_interval=p.get('pr_interval') or None,
            qrs_duration=p.get('qrs_duration') or None,
            qt_interval=p.get('qt_interval') or None,
            qtc_interval=p.get('qtc_interval') or None,
            st_changes=p.get('st_changes') == 'on',
            st_details=p.get('st_details', ''),
            t_wave_changes=p.get('t_wave_changes') == 'on',
            t_wave_details=p.get('t_wave_details', ''),
            lbbb=p.get('lbbb') == 'on',
            rbbb=p.get('rbbb') == 'on',
            lvh=p.get('lvh') == 'on',
            rvh=p.get('rvh') == 'on',
            q_waves=p.get('q_waves') == 'on',
            q_wave_details=p.get('q_wave_details', ''),
            interpretation=p.get('interpretation', ''),
            notes=p.get('notes', ''),
        )
        messages.success(request, 'ECG record saved.')
        return redirect('cardiology_consultation_detail', pk=consult.pk)

    return render(request, 'cardiology/ecg_form.html', {
        'consultation': consult,
        'rhythm_choices': ECGRecord.Rhythm.choices,
        'axis_choices': ECGRecord.Axis.choices,
        'today': date.today().isoformat(),
    })


# ── Echo Reports ──────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_echo_report')
def echo_report_create(request, consultation_pk):
    consult = get_object_or_404(CardiologyConsultation, pk=consultation_pk)
    if request.method == 'POST':
        p = request.POST
        EchoReport.objects.create(
            consultation=consult,
            patient=consult.patient,
            study_date=p.get('study_date') or date.today(),
            echo_type=p.get('echo_type', 'tte'),
            performed_by=request.user if request.user.is_authenticated else None,
            lvedd=p.get('lvedd') or None,
            lvesd=p.get('lvesd') or None,
            ivsd=p.get('ivsd') or None,
            pwd=p.get('pwd') or None,
            lv_mass=p.get('lv_mass') or None,
            ef_percent=p.get('ef_percent') or None,
            lv_function=p.get('lv_function', ''),
            wall_motion=p.get('wall_motion', ''),
            diastolic_function=p.get('diastolic_function', ''),
            la_size=p.get('la_size') or None,
            ra_size=p.get('ra_size', ''),
            rvedd=p.get('rvedd') or None,
            rv_function=p.get('rv_function', ''),
            tapse=p.get('tapse') or None,
            pasp=p.get('pasp') or None,
            aortic_valve=p.get('aortic_valve', ''),
            mitral_valve=p.get('mitral_valve', ''),
            tricuspid_valve=p.get('tricuspid_valve', ''),
            pulmonary_valve=p.get('pulmonary_valve', ''),
            pericardium=p.get('pericardium', ''),
            aortic_root=p.get('aortic_root') or None,
            impression=p.get('impression', ''),
            recommendation=p.get('recommendation', ''),
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Echo report saved.')
        return redirect('cardiology_consultation_detail', pk=consult.pk)

    return render(request, 'cardiology/echo_form.html', {
        'consultation': consult,
        'echo_type_choices': EchoReport.EchoType.choices,
        'lv_function_choices': EchoReport.LVFunction.choices,
        'today': date.today().isoformat(),
    })


# ── Cardiac Procedures ────────────────────────────────────────────────────────

@hms_permission_required('core.manage_cardiac_procedure')
def cardiac_procedure_create(request, consultation_pk):
    consult = get_object_or_404(CardiologyConsultation, pk=consultation_pk)
    if request.method == 'POST':
        p = request.POST
        CardiacProcedure.objects.create(
            consultation=consult,
            patient=consult.patient,
            procedure_date=p.get('procedure_date') or date.today(),
            procedure_type=p.get('procedure_type', 'other'),
            performed_by=request.user if request.user.is_authenticated else None,
            assistant=p.get('assistant', ''),
            indication=p.get('indication', ''),
            technique=p.get('technique', ''),
            findings=p.get('findings', ''),
            outcome=p.get('outcome', 'success'),
            complications=p.get('complications', ''),
            post_procedure_plan=p.get('post_procedure_plan', ''),
            contrast_used=p.get('contrast_used', ''),
            radiation_dose=p.get('radiation_dose', ''),
            notes=p.get('notes', ''),
        )
        messages.success(request, 'Procedure recorded.')
        return redirect('cardiology_consultation_detail', pk=consult.pk)

    return render(request, 'cardiology/procedure_form.html', {
        'consultation': consult,
        'procedure_type_choices': CardiacProcedure.ProcedureType.choices,
        'outcome_choices': CardiacProcedure.Outcome.choices,
        'today': date.today().isoformat(),
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_cardiology_reports')
def cardiology_reports(request):
    today = date.today()
    date_from = request.GET.get('date_from', (today - timedelta(days=30)).isoformat())
    date_to = request.GET.get('date_to', today.isoformat())
    report_type = request.GET.get('report_type', 'summary')

    qs = CardiologyConsultation.objects.filter(
        consultation_date__gte=date_from, consultation_date__lte=date_to
    )

    by_clinic = list(qs.values('clinic_type').annotate(n=Count('id')).order_by('-n'))
    clinic_map = dict(CardiologyConsultation.ClinicType.choices)
    for r in by_clinic:
        r['label'] = clinic_map.get(r['clinic_type'], r['clinic_type'])

    by_nyha = list(qs.exclude(nyha_class='na').values('nyha_class').annotate(n=Count('id')).order_by('nyha_class'))
    nyha_map = dict(CardiologyConsultation.NYHAClass.choices)
    for r in by_nyha:
        r['label'] = nyha_map.get(r['nyha_class'], r['nyha_class'])

    # Risk factor prevalence
    risk_fields = ['hypertension', 'diabetes', 'dyslipidaemia', 'smoking', 'obesity', 'family_hx_cad', 'ckd']
    risk_labels = ['Hypertension', 'Diabetes', 'Dyslipidaemia', 'Smoking', 'Obesity', 'Family Hx CAD', 'CKD']
    risk_data = [{'label': lbl, 'n': qs.filter(**{f: True}).count()} for f, lbl in zip(risk_fields, risk_labels)]

    echos = EchoReport.objects.filter(study_date__gte=date_from, study_date__lte=date_to)
    by_lv = list(echos.exclude(lv_function='').values('lv_function').annotate(n=Count('id')).order_by('-n'))
    lv_map = dict(EchoReport.LVFunction.choices)
    for r in by_lv:
        r['label'] = lv_map.get(r['lv_function'], r['lv_function'])

    return render(request, 'cardiology/reports.html', {
        'date_from': date_from, 'date_to': date_to, 'report_type': report_type,
        'total': qs.count(),
        'echo_count': echos.count(),
        'ecg_count': ECGRecord.objects.filter(recording_date__gte=date_from, recording_date__lte=date_to).count(),
        'proc_count': CardiacProcedure.objects.filter(procedure_date__gte=date_from, procedure_date__lte=date_to).count(),
        'by_clinic': by_clinic,
        'by_nyha': by_nyha,
        'risk_data': risk_data,
        'by_lv': by_lv,
        'consultations': qs.select_related('patient').order_by('-consultation_date')[:200] if report_type == 'detail' else [],
    })


# ── Patient Search API ────────────────────────────────────────────────────────

@hms_permission_required('core.view_cardiology_consultation')
def cardiology_patient_search(request):
    return JsonResponse(_patient_search(request.GET.get('q', '')), safe=False)
