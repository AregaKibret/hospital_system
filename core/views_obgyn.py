from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import hms_permission_required
from .models import (
    ANCVisit, DeliveryRecord, FamilyPlanningVisit, GynecologyConsultation,
    InfertilityCase, NewbornRecord, Patient, PNCVisit, Pregnancy, Visit,
)

User = get_user_model()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clinicians():
    return User.objects.filter(is_active=True).order_by('last_name', 'first_name')


def _patient_search_qs(q):
    return Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(middle_name__icontains=q) | Q(card_number__icontains=q) |
        Q(mobile__icontains=q)
    ).filter(is_active=True)[:30]


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_obgyn_dashboard')
def obgyn_dashboard(request):
    today = date.today()
    thirty_days = today + timedelta(days=30)

    active_pregnancies = Pregnancy.objects.filter(status='active').count()
    anc_today = ANCVisit.objects.filter(visit_date=today).count()
    deliveries_this_month = DeliveryRecord.objects.filter(
        delivery_date__year=today.year, delivery_date__month=today.month
    ).count()
    pnc_today = PNCVisit.objects.filter(visit_date=today).count()
    due_soon = Pregnancy.objects.filter(status='active', edd__lte=thirty_days, edd__gte=today).count()
    fp_today = FamilyPlanningVisit.objects.filter(visit_date=today).count()
    gyn_today = GynecologyConsultation.objects.filter(consultation_date=today).count()

    recent_pregnancies = Pregnancy.objects.select_related('patient', 'registered_by').filter(
        status='active'
    ).order_by('-created_at')[:8]

    recent_deliveries = DeliveryRecord.objects.select_related(
        'pregnancy__patient', 'attended_by'
    ).order_by('-delivery_date', '-created_at')[:6]

    return render(request, 'obgyn/dashboard.html', {
        'active_pregnancies': active_pregnancies,
        'anc_today': anc_today,
        'deliveries_this_month': deliveries_this_month,
        'pnc_today': pnc_today,
        'due_soon': due_soon,
        'fp_today': fp_today,
        'gyn_today': gyn_today,
        'recent_pregnancies': recent_pregnancies,
        'recent_deliveries': recent_deliveries,
    })


# ── Pregnancy ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_pregnancy')
def pregnancy_list(request):
    qs = Pregnancy.objects.select_related('patient', 'registered_by').all()
    status_filter = request.GET.get('status', 'active')
    if status_filter:
        qs = qs.filter(status=status_filter)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q) |
            Q(registration_number__icontains=q)
        )
    paginator = Paginator(qs.order_by('-created_at'), 25)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'obgyn/pregnancy_list.html', {
        'page_obj': page,
        'status_filter': status_filter,
        'q': q,
        'status_choices': Pregnancy.Status.choices,
    })


@hms_permission_required('core.view_pregnancy')
def pregnancy_detail(request, pk):
    pregnancy = get_object_or_404(Pregnancy.objects.select_related('patient', 'registered_by'), pk=pk)
    anc_visits = pregnancy.anc_visits.select_related('seen_by').order_by('visit_date')
    delivery = getattr(pregnancy, 'delivery', None)
    newborns = delivery.newborns.all() if delivery else []
    pnc_visits = delivery.pnc_visits.select_related('seen_by').order_by('visit_date') if delivery else []
    return render(request, 'obgyn/pregnancy_detail.html', {
        'pregnancy': pregnancy,
        'anc_visits': anc_visits,
        'delivery': delivery,
        'newborns': newborns,
        'pnc_visits': pnc_visits,
    })


@hms_permission_required('core.manage_pregnancy')
def pregnancy_register(request):
    if request.method == 'POST':
        p = request.POST
        patient_id = p.get('patient_id')
        patient = get_object_or_404(Patient, pk=patient_id)
        lmp_str = p.get('lmp', '').strip()
        lmp = date.fromisoformat(lmp_str) if lmp_str else None
        edd_str = p.get('edd', '').strip()
        edd = date.fromisoformat(edd_str) if edd_str else None
        edd_scan_str = p.get('edd_by_scan', '').strip()
        edd_by_scan = date.fromisoformat(edd_scan_str) if edd_scan_str else None

        preg = Pregnancy.objects.create(
            patient=patient,
            lmp=lmp,
            edd=edd,
            edd_by_scan=edd_by_scan,
            gravida=int(p.get('gravida', 1) or 1),
            para=int(p.get('para', 0) or 0),
            abortion=int(p.get('abortion', 0) or 0),
            living_children=int(p.get('living_children', 0) or 0),
            blood_group=p.get('blood_group', ''),
            rhesus=p.get('rhesus', 'Unknown'),
            height_cm=p.get('height_cm') or None,
            pre_pregnancy_weight_kg=p.get('pre_pregnancy_weight_kg') or None,
            risk_factors=p.get('risk_factors', '').strip(),
            medical_history=p.get('medical_history', '').strip(),
            surgical_history=p.get('surgical_history', '').strip(),
            family_history=p.get('family_history', '').strip(),
            allergies=p.get('allergies', '').strip(),
            notes=p.get('notes', '').strip(),
            registered_by=request.user,
        )
        messages.success(request, f'Pregnancy registered — {preg.registration_number}')
        return redirect('pregnancy_detail', pk=preg.pk)

    q = request.GET.get('q', '').strip()
    patients = _patient_search_qs(q) if q else []
    selected_patient = None
    pid = request.GET.get('patient')
    if pid:
        selected_patient = get_object_or_404(Patient, pk=pid)

    return render(request, 'obgyn/pregnancy_form.html', {
        'patients': patients,
        'selected_patient': selected_patient,
        'q': q,
        'blood_groups': Pregnancy.BloodGroup.choices,
        'action': 'Register',
    })


@hms_permission_required('core.manage_pregnancy')
def pregnancy_edit(request, pk):
    pregnancy = get_object_or_404(Pregnancy, pk=pk)
    if request.method == 'POST':
        p = request.POST
        lmp_str = p.get('lmp', '').strip()
        pregnancy.lmp = date.fromisoformat(lmp_str) if lmp_str else None
        edd_str = p.get('edd', '').strip()
        pregnancy.edd = date.fromisoformat(edd_str) if edd_str else None
        edd_scan_str = p.get('edd_by_scan', '').strip()
        pregnancy.edd_by_scan = date.fromisoformat(edd_scan_str) if edd_scan_str else None
        pregnancy.gravida = int(p.get('gravida', 1) or 1)
        pregnancy.para = int(p.get('para', 0) or 0)
        pregnancy.abortion = int(p.get('abortion', 0) or 0)
        pregnancy.living_children = int(p.get('living_children', 0) or 0)
        pregnancy.blood_group = p.get('blood_group', '')
        pregnancy.rhesus = p.get('rhesus', 'Unknown')
        pregnancy.height_cm = p.get('height_cm') or None
        pregnancy.pre_pregnancy_weight_kg = p.get('pre_pregnancy_weight_kg') or None
        pregnancy.risk_factors = p.get('risk_factors', '').strip()
        pregnancy.medical_history = p.get('medical_history', '').strip()
        pregnancy.surgical_history = p.get('surgical_history', '').strip()
        pregnancy.family_history = p.get('family_history', '').strip()
        pregnancy.allergies = p.get('allergies', '').strip()
        pregnancy.status = p.get('status', pregnancy.status)
        pregnancy.notes = p.get('notes', '').strip()
        pregnancy.save()
        messages.success(request, 'Pregnancy record updated.')
        return redirect('pregnancy_detail', pk=pregnancy.pk)

    return render(request, 'obgyn/pregnancy_form.html', {
        'pregnancy': pregnancy,
        'blood_groups': Pregnancy.BloodGroup.choices,
        'status_choices': Pregnancy.Status.choices,
        'action': 'Edit',
    })


# ── ANC Visits ────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_anc_visit')
def anc_visit_create(request, pregnancy_pk):
    pregnancy = get_object_or_404(Pregnancy, pk=pregnancy_pk)
    last_num = pregnancy.anc_visits.order_by('-visit_number').values_list('visit_number', flat=True).first() or 0

    if request.method == 'POST':
        p = request.POST
        visit_date_str = p.get('visit_date', '').strip()
        next_visit_str = p.get('next_visit_date', '').strip()
        ANCVisit.objects.create(
            pregnancy=pregnancy,
            visit_number=last_num + 1,
            visit_date=date.fromisoformat(visit_date_str) if visit_date_str else date.today(),
            ga_weeks=p.get('ga_weeks') or None,
            ga_days=p.get('ga_days') or 0,
            weight_kg=p.get('weight_kg') or None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            pulse=p.get('pulse') or None,
            temperature=p.get('temperature') or None,
            fundal_height_cm=p.get('fundal_height_cm') or None,
            presentation=p.get('presentation', 'unknown'),
            fetal_heart_rate=p.get('fetal_heart_rate') or None,
            fhr_status=p.get('fhr_status', 'na'),
            fetal_movement=p.get('fetal_movement') == '1' if p.get('fetal_movement') else None,
            oedema=p.get('oedema', '').strip(),
            urine_protein=p.get('urine_protein', ''),
            urine_glucose=p.get('urine_glucose', ''),
            hb_level=p.get('hb_level') or None,
            iron_given=bool(p.get('iron_given')),
            folic_given=bool(p.get('folic_given')),
            tt_given=bool(p.get('tt_given')),
            ipt_given=bool(p.get('ipt_given')),
            llin_given=bool(p.get('llin_given')),
            clinical_notes=p.get('clinical_notes', '').strip(),
            management_plan=p.get('management_plan', '').strip(),
            next_visit_date=date.fromisoformat(next_visit_str) if next_visit_str else None,
            seen_by=request.user,
        )
        messages.success(request, f'ANC Visit #{last_num + 1} recorded.')
        return redirect('pregnancy_detail', pk=pregnancy.pk)

    return render(request, 'obgyn/anc_visit_form.html', {
        'pregnancy': pregnancy,
        'visit_number': last_num + 1,
        'today': date.today(),
        'presentation_choices': ANCVisit.Presentation.choices,
        'fhr_choices': ANCVisit.FHRStatus.choices,
    })


@hms_permission_required('core.manage_anc_visit')
def anc_visit_edit(request, pk):
    anc = get_object_or_404(ANCVisit.objects.select_related('pregnancy'), pk=pk)
    if request.method == 'POST':
        p = request.POST
        visit_date_str = p.get('visit_date', '').strip()
        next_visit_str = p.get('next_visit_date', '').strip()
        anc.visit_date = date.fromisoformat(visit_date_str) if visit_date_str else anc.visit_date
        anc.ga_weeks = p.get('ga_weeks') or None
        anc.ga_days = p.get('ga_days') or 0
        anc.weight_kg = p.get('weight_kg') or None
        anc.bp_systolic = p.get('bp_systolic') or None
        anc.bp_diastolic = p.get('bp_diastolic') or None
        anc.pulse = p.get('pulse') or None
        anc.temperature = p.get('temperature') or None
        anc.fundal_height_cm = p.get('fundal_height_cm') or None
        anc.presentation = p.get('presentation', anc.presentation)
        anc.fetal_heart_rate = p.get('fetal_heart_rate') or None
        anc.fhr_status = p.get('fhr_status', anc.fhr_status)
        anc.oedema = p.get('oedema', '').strip()
        anc.urine_protein = p.get('urine_protein', '')
        anc.urine_glucose = p.get('urine_glucose', '')
        anc.hb_level = p.get('hb_level') or None
        anc.iron_given = bool(p.get('iron_given'))
        anc.folic_given = bool(p.get('folic_given'))
        anc.tt_given = bool(p.get('tt_given'))
        anc.ipt_given = bool(p.get('ipt_given'))
        anc.llin_given = bool(p.get('llin_given'))
        anc.clinical_notes = p.get('clinical_notes', '').strip()
        anc.management_plan = p.get('management_plan', '').strip()
        anc.next_visit_date = date.fromisoformat(next_visit_str) if next_visit_str else None
        anc.save()
        messages.success(request, 'ANC visit updated.')
        return redirect('pregnancy_detail', pk=anc.pregnancy_id)

    return render(request, 'obgyn/anc_visit_form.html', {
        'pregnancy': anc.pregnancy,
        'anc': anc,
        'visit_number': anc.visit_number,
        'today': date.today(),
        'presentation_choices': ANCVisit.Presentation.choices,
        'fhr_choices': ANCVisit.FHRStatus.choices,
        'edit': True,
    })


# ── Delivery ──────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_delivery')
def delivery_record_create(request, pregnancy_pk):
    pregnancy = get_object_or_404(Pregnancy, pk=pregnancy_pk)
    if hasattr(pregnancy, 'delivery'):
        messages.warning(request, 'A delivery record already exists for this pregnancy.')
        return redirect('pregnancy_detail', pk=pregnancy.pk)

    if request.method == 'POST':
        p = request.POST
        delivery_date_str = p.get('delivery_date', '').strip()
        delivery_time_str = p.get('delivery_time', '').strip()
        delivery = DeliveryRecord.objects.create(
            pregnancy=pregnancy,
            delivery_date=date.fromisoformat(delivery_date_str) if delivery_date_str else date.today(),
            delivery_time=delivery_time_str or None,
            delivery_mode=p.get('delivery_mode', 'svd'),
            outcome=p.get('outcome', 'live_birth'),
            ga_at_delivery=p.get('ga_at_delivery') or None,
            blood_loss_ml=p.get('blood_loss_ml') or None,
            placenta_complete=p.get('placenta_complete') == '1' if p.get('placenta_complete') else None,
            episiotomy=p.get('episiotomy', 'none'),
            tear_degree=p.get('tear_degree', '').strip(),
            repair_done=p.get('repair_done') == '1' if p.get('repair_done') else None,
            complications=p.get('complications', '').strip(),
            oxytocin_given=bool(p.get('oxytocin_given')),
            bp_systolic_delivery=p.get('bp_systolic_delivery') or None,
            bp_diastolic_delivery=p.get('bp_diastolic_delivery') or None,
            notes=p.get('notes', '').strip(),
            attended_by=request.user,
        )
        pregnancy.status = 'delivered'
        pregnancy.save(update_fields=['status'])

        # Create newborn(s)
        count = int(p.get('newborn_count', 1) or 1)
        for i in range(1, count + 1):
            sex = p.get(f'nb_sex_{i}', '')
            if not sex:
                continue
            NewbornRecord.objects.create(
                delivery=delivery,
                birth_order=i,
                sex=sex,
                birth_weight_g=p.get(f'nb_weight_{i}') or None,
                birth_length_cm=p.get(f'nb_length_{i}') or None,
                head_circumference_cm=p.get(f'nb_hc_{i}') or None,
                apgar_1min=p.get(f'nb_apgar1_{i}') or None,
                apgar_5min=p.get(f'nb_apgar5_{i}') or None,
                apgar_10min=p.get(f'nb_apgar10_{i}') or None,
                condition=p.get(f'nb_condition_{i}', 'alive'),
                resuscitation=bool(p.get(f'nb_resus_{i}')),
                vitamin_k_given=bool(p.get(f'nb_vitk_{i}')),
                eye_prophylaxis=bool(p.get(f'nb_eye_{i}')),
                bcg_given=bool(p.get(f'nb_bcg_{i}')),
                hepatitis_b_given=bool(p.get(f'nb_hepb_{i}')),
            )

        messages.success(request, 'Delivery record saved.')
        return redirect('pregnancy_detail', pk=pregnancy.pk)

    return render(request, 'obgyn/delivery_form.html', {
        'pregnancy': pregnancy,
        'today': date.today(),
        'delivery_mode_choices': DeliveryRecord.DeliveryMode.choices,
        'outcome_choices': DeliveryRecord.Outcome.choices,
        'episiotomy_choices': DeliveryRecord.Episiotomy.choices,
        'newborn_condition_choices': NewbornRecord.Condition.choices,
        'sex_choices': NewbornRecord.Sex.choices,
    })


@hms_permission_required('core.manage_delivery')
def delivery_record_edit(request, pk):
    delivery = get_object_or_404(DeliveryRecord.objects.select_related('pregnancy'), pk=pk)
    if request.method == 'POST':
        p = request.POST
        delivery_date_str = p.get('delivery_date', '').strip()
        delivery.delivery_date = date.fromisoformat(delivery_date_str) if delivery_date_str else delivery.delivery_date
        delivery.delivery_time = p.get('delivery_time', '') or None
        delivery.delivery_mode = p.get('delivery_mode', delivery.delivery_mode)
        delivery.outcome = p.get('outcome', delivery.outcome)
        delivery.ga_at_delivery = p.get('ga_at_delivery') or None
        delivery.blood_loss_ml = p.get('blood_loss_ml') or None
        delivery.episiotomy = p.get('episiotomy', delivery.episiotomy)
        delivery.tear_degree = p.get('tear_degree', '').strip()
        delivery.complications = p.get('complications', '').strip()
        delivery.oxytocin_given = bool(p.get('oxytocin_given'))
        delivery.bp_systolic_delivery = p.get('bp_systolic_delivery') or None
        delivery.bp_diastolic_delivery = p.get('bp_diastolic_delivery') or None
        delivery.notes = p.get('notes', '').strip()
        delivery.save()
        messages.success(request, 'Delivery record updated.')
        return redirect('pregnancy_detail', pk=delivery.pregnancy_id)

    return render(request, 'obgyn/delivery_form.html', {
        'pregnancy': delivery.pregnancy,
        'delivery': delivery,
        'today': date.today(),
        'delivery_mode_choices': DeliveryRecord.DeliveryMode.choices,
        'outcome_choices': DeliveryRecord.Outcome.choices,
        'episiotomy_choices': DeliveryRecord.Episiotomy.choices,
        'newborn_condition_choices': NewbornRecord.Condition.choices,
        'sex_choices': NewbornRecord.Sex.choices,
        'edit': True,
    })


# ── PNC Visits ────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_pnc_visit')
def pnc_visit_create(request, delivery_pk):
    delivery = get_object_or_404(DeliveryRecord.objects.select_related('pregnancy'), pk=delivery_pk)
    last_num = delivery.pnc_visits.order_by('-visit_number').values_list('visit_number', flat=True).first() or 0

    if request.method == 'POST':
        p = request.POST
        visit_date_str = p.get('visit_date', '').strip()
        next_visit_str = p.get('next_visit_date', '').strip()
        vdate = date.fromisoformat(visit_date_str) if visit_date_str else date.today()
        days_pp = (vdate - delivery.delivery_date).days if delivery.delivery_date else None
        PNCVisit.objects.create(
            delivery=delivery,
            visit_number=last_num + 1,
            visit_date=vdate,
            days_postpartum=days_pp,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            temperature=p.get('temperature') or None,
            weight_kg=p.get('weight_kg') or None,
            lochia=p.get('lochia', '').strip(),
            uterus_involution=p.get('uterus_involution', '').strip(),
            perineum=p.get('perineum', '').strip(),
            breastfeeding=p.get('breastfeeding') == '1' if p.get('breastfeeding') else None,
            breast_condition=p.get('breast_condition', '').strip(),
            newborn_condition=p.get('newborn_condition', '').strip(),
            family_planning_counseled=bool(p.get('family_planning_counseled')),
            fp_method_chosen=p.get('fp_method_chosen', '').strip(),
            immunization_updated=bool(p.get('immunization_updated')),
            hiv_test_done=bool(p.get('hiv_test_done')),
            status=p.get('status', 'normal'),
            clinical_notes=p.get('clinical_notes', '').strip(),
            next_visit_date=date.fromisoformat(next_visit_str) if next_visit_str else None,
            seen_by=request.user,
        )
        messages.success(request, f'PNC Visit #{last_num + 1} recorded.')
        return redirect('pregnancy_detail', pk=delivery.pregnancy_id)

    return render(request, 'obgyn/pnc_visit_form.html', {
        'delivery': delivery,
        'visit_number': last_num + 1,
        'today': date.today(),
        'status_choices': PNCVisit.Status.choices,
    })


@hms_permission_required('core.manage_pnc_visit')
def pnc_visit_edit(request, pk):
    pnc = get_object_or_404(PNCVisit.objects.select_related('delivery__pregnancy'), pk=pk)
    if request.method == 'POST':
        p = request.POST
        visit_date_str = p.get('visit_date', '').strip()
        next_visit_str = p.get('next_visit_date', '').strip()
        pnc.visit_date = date.fromisoformat(visit_date_str) if visit_date_str else pnc.visit_date
        pnc.bp_systolic = p.get('bp_systolic') or None
        pnc.bp_diastolic = p.get('bp_diastolic') or None
        pnc.temperature = p.get('temperature') or None
        pnc.weight_kg = p.get('weight_kg') or None
        pnc.lochia = p.get('lochia', '').strip()
        pnc.uterus_involution = p.get('uterus_involution', '').strip()
        pnc.perineum = p.get('perineum', '').strip()
        pnc.breastfeeding = p.get('breastfeeding') == '1' if p.get('breastfeeding') else None
        pnc.breast_condition = p.get('breast_condition', '').strip()
        pnc.newborn_condition = p.get('newborn_condition', '').strip()
        pnc.family_planning_counseled = bool(p.get('family_planning_counseled'))
        pnc.fp_method_chosen = p.get('fp_method_chosen', '').strip()
        pnc.immunization_updated = bool(p.get('immunization_updated'))
        pnc.status = p.get('status', pnc.status)
        pnc.clinical_notes = p.get('clinical_notes', '').strip()
        pnc.next_visit_date = date.fromisoformat(next_visit_str) if next_visit_str else None
        pnc.save()
        messages.success(request, 'PNC visit updated.')
        return redirect('pregnancy_detail', pk=pnc.delivery.pregnancy_id)

    return render(request, 'obgyn/pnc_visit_form.html', {
        'delivery': pnc.delivery,
        'pnc': pnc,
        'visit_number': pnc.visit_number,
        'today': date.today(),
        'status_choices': PNCVisit.Status.choices,
        'edit': True,
    })


# ── Gynecology Consultations ──────────────────────────────────────────────────

@hms_permission_required('core.view_gyn_consultation')
def gyn_consultation_list(request):
    qs = GynecologyConsultation.objects.select_related('patient', 'seen_by').all()
    q = request.GET.get('q', '').strip()
    cat = request.GET.get('category', '')
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if cat:
        qs = qs.filter(category=cat)
    paginator = Paginator(qs.order_by('-consultation_date', '-created_at'), 25)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'obgyn/gyn_list.html', {
        'page_obj': page,
        'q': q,
        'category_filter': cat,
        'category_choices': GynecologyConsultation.Category.choices,
    })


@hms_permission_required('core.manage_gyn_consultation')
def gyn_consultation_create(request):
    if request.method == 'POST':
        p = request.POST
        patient = get_object_or_404(Patient, pk=p.get('patient_id'))
        date_str = p.get('consultation_date', '').strip()
        fu_str = p.get('follow_up_date', '').strip()
        lmp_str = p.get('lmp', '').strip()
        GynecologyConsultation.objects.create(
            patient=patient,
            consultation_date=date.fromisoformat(date_str) if date_str else date.today(),
            category=p.get('category', 'other'),
            chief_complaint=p.get('chief_complaint', '').strip(),
            menarche_age=p.get('menarche_age') or None,
            cycle_length_days=p.get('cycle_length_days') or None,
            menstrual_duration_days=p.get('menstrual_duration_days') or None,
            lmp=date.fromisoformat(lmp_str) if lmp_str else None,
            menstrual_pattern=p.get('menstrual_pattern', '').strip(),
            dysmenorrhoea=p.get('dysmenorrhoea') == '1' if p.get('dysmenorrhoea') else None,
            gravida=p.get('gravida') or None,
            para=p.get('para') or None,
            current_contraception=p.get('current_contraception', '').strip(),
            bmi=p.get('bmi') or None,
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            per_speculum=p.get('per_speculum', '').strip(),
            per_vaginum=p.get('per_vaginum', '').strip(),
            uterus_size=p.get('uterus_size', '').strip(),
            adnexa=p.get('adnexa', '').strip(),
            investigations_ordered=p.get('investigations_ordered', '').strip(),
            diagnosis=p.get('diagnosis', '').strip(),
            treatment_plan=p.get('treatment_plan', '').strip(),
            via_result=p.get('via_result', '').strip(),
            pap_result=p.get('pap_result', '').strip(),
            referral=p.get('referral', '').strip(),
            follow_up_date=date.fromisoformat(fu_str) if fu_str else None,
            seen_by=request.user,
        )
        messages.success(request, 'Gynecology consultation recorded.')
        return redirect('gyn_consultation_list')

    q = request.GET.get('q', '').strip()
    patients = _patient_search_qs(q) if q else []
    selected_patient = None
    pid = request.GET.get('patient')
    if pid:
        selected_patient = get_object_or_404(Patient, pk=pid)

    return render(request, 'obgyn/gyn_form.html', {
        'patients': patients,
        'selected_patient': selected_patient,
        'q': q,
        'category_choices': GynecologyConsultation.Category.choices,
        'today': date.today(),
        'action': 'New',
    })


@hms_permission_required('core.manage_gyn_consultation')
def gyn_consultation_edit(request, pk):
    gyn = get_object_or_404(GynecologyConsultation.objects.select_related('patient'), pk=pk)
    if request.method == 'POST':
        p = request.POST
        date_str = p.get('consultation_date', '').strip()
        fu_str = p.get('follow_up_date', '').strip()
        lmp_str = p.get('lmp', '').strip()
        gyn.consultation_date = date.fromisoformat(date_str) if date_str else gyn.consultation_date
        gyn.category = p.get('category', gyn.category)
        gyn.chief_complaint = p.get('chief_complaint', '').strip()
        gyn.menarche_age = p.get('menarche_age') or None
        gyn.cycle_length_days = p.get('cycle_length_days') or None
        gyn.menstrual_duration_days = p.get('menstrual_duration_days') or None
        gyn.lmp = date.fromisoformat(lmp_str) if lmp_str else None
        gyn.menstrual_pattern = p.get('menstrual_pattern', '').strip()
        gyn.gravida = p.get('gravida') or None
        gyn.para = p.get('para') or None
        gyn.current_contraception = p.get('current_contraception', '').strip()
        gyn.bmi = p.get('bmi') or None
        gyn.bp_systolic = p.get('bp_systolic') or None
        gyn.bp_diastolic = p.get('bp_diastolic') or None
        gyn.per_speculum = p.get('per_speculum', '').strip()
        gyn.per_vaginum = p.get('per_vaginum', '').strip()
        gyn.uterus_size = p.get('uterus_size', '').strip()
        gyn.adnexa = p.get('adnexa', '').strip()
        gyn.investigations_ordered = p.get('investigations_ordered', '').strip()
        gyn.diagnosis = p.get('diagnosis', '').strip()
        gyn.treatment_plan = p.get('treatment_plan', '').strip()
        gyn.via_result = p.get('via_result', '').strip()
        gyn.pap_result = p.get('pap_result', '').strip()
        gyn.referral = p.get('referral', '').strip()
        gyn.follow_up_date = date.fromisoformat(fu_str) if fu_str else None
        gyn.save()
        messages.success(request, 'Consultation updated.')
        return redirect('gyn_consultation_list')

    return render(request, 'obgyn/gyn_form.html', {
        'gyn': gyn,
        'selected_patient': gyn.patient,
        'category_choices': GynecologyConsultation.Category.choices,
        'today': date.today(),
        'action': 'Edit',
    })


@hms_permission_required('core.view_gyn_consultation')
def gyn_consultation_detail(request, pk):
    gyn = get_object_or_404(GynecologyConsultation.objects.select_related('patient', 'seen_by'), pk=pk)
    return render(request, 'obgyn/gyn_detail.html', {'gyn': gyn})


# ── Family Planning ───────────────────────────────────────────────────────────

@hms_permission_required('core.view_family_planning')
def fp_visit_list(request):
    qs = FamilyPlanningVisit.objects.select_related('patient', 'seen_by').all()
    q = request.GET.get('q', '').strip()
    method = request.GET.get('method', '')
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if method:
        qs = qs.filter(method=method)
    paginator = Paginator(qs.order_by('-visit_date'), 25)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'obgyn/fp_list.html', {
        'page_obj': page,
        'q': q,
        'method_filter': method,
        'method_choices': FamilyPlanningVisit.Method.choices,
    })


@hms_permission_required('core.manage_family_planning')
def fp_visit_create(request):
    if request.method == 'POST':
        p = request.POST
        patient = get_object_or_404(Patient, pk=p.get('patient_id'))
        visit_date_str = p.get('visit_date', '').strip()
        next_visit_str = p.get('next_visit_date', '').strip()
        lmp_str = p.get('lmp', '').strip()
        FamilyPlanningVisit.objects.create(
            patient=patient,
            visit_date=date.fromisoformat(visit_date_str) if visit_date_str else date.today(),
            visit_type=p.get('visit_type', 'new'),
            method=p.get('method', 'none'),
            previous_method=p.get('previous_method', ''),
            counseling_done=bool(p.get('counseling_done')),
            bp_systolic=p.get('bp_systolic') or None,
            bp_diastolic=p.get('bp_diastolic') or None,
            weight_kg=p.get('weight_kg') or None,
            lmp=date.fromisoformat(lmp_str) if lmp_str else None,
            pregnancy_test=p.get('pregnancy_test', ''),
            complications=p.get('complications', '').strip(),
            side_effects=p.get('side_effects', '').strip(),
            notes=p.get('notes', '').strip(),
            next_visit_date=date.fromisoformat(next_visit_str) if next_visit_str else None,
            seen_by=request.user,
        )
        messages.success(request, 'Family planning visit recorded.')
        return redirect('fp_visit_list')

    q = request.GET.get('q', '').strip()
    patients = _patient_search_qs(q) if q else []
    selected_patient = None
    pid = request.GET.get('patient')
    if pid:
        selected_patient = get_object_or_404(Patient, pk=pid)

    return render(request, 'obgyn/fp_form.html', {
        'patients': patients,
        'selected_patient': selected_patient,
        'q': q,
        'method_choices': FamilyPlanningVisit.Method.choices,
        'visit_type_choices': FamilyPlanningVisit.VisitType.choices,
        'today': date.today(),
        'action': 'New',
    })


# ── Infertility Cases ─────────────────────────────────────────────────────────

@hms_permission_required('core.view_infertility_case')
def infertility_case_list(request):
    qs = InfertilityCase.objects.select_related('patient', 'opened_by').all()
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    paginator = Paginator(qs.order_by('-created_at'), 25)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'obgyn/infertility_list.html', {
        'page_obj': page,
        'q': q,
        'status_filter': status,
        'status_choices': InfertilityCase.Status.choices,
    })


@hms_permission_required('core.manage_infertility_case')
def infertility_case_create(request):
    if request.method == 'POST':
        p = request.POST
        patient = get_object_or_404(Patient, pk=p.get('patient_id'))
        InfertilityCase.objects.create(
            patient=patient,
            partner_name=p.get('partner_name', '').strip(),
            partner_contact=p.get('partner_contact', '').strip(),
            infertility_type=p.get('infertility_type', 'primary'),
            duration_years=p.get('duration_years') or None,
            status=p.get('status', 'active'),
            menstrual_history=p.get('menstrual_history', '').strip(),
            tubal_factor=p.get('tubal_factor') == '1' if p.get('tubal_factor') else None,
            ovulatory_factor=p.get('ovulatory_factor') == '1' if p.get('ovulatory_factor') else None,
            uterine_factor=p.get('uterine_factor') == '1' if p.get('uterine_factor') else None,
            male_factor=p.get('male_factor') == '1' if p.get('male_factor') else None,
            semen_analysis=p.get('semen_analysis', '').strip(),
            hsg_result=p.get('hsg_result', '').strip(),
            hormone_profile=p.get('hormone_profile', '').strip(),
            ultrasound_findings=p.get('ultrasound_findings', '').strip(),
            treatment_plan=p.get('treatment_plan', '').strip(),
            ovulation_induction=bool(p.get('ovulation_induction')),
            iui_done=bool(p.get('iui_done')),
            notes=p.get('notes', '').strip(),
            opened_by=request.user,
        )
        messages.success(request, 'Infertility case opened.')
        return redirect('infertility_case_list')

    q = request.GET.get('q', '').strip()
    patients = _patient_search_qs(q) if q else []
    selected_patient = None
    pid = request.GET.get('patient')
    if pid:
        selected_patient = get_object_or_404(Patient, pk=pid)

    return render(request, 'obgyn/infertility_form.html', {
        'patients': patients,
        'selected_patient': selected_patient,
        'q': q,
        'type_choices': InfertilityCase.Type.choices,
        'status_choices': InfertilityCase.Status.choices,
        'action': 'Open',
    })


@hms_permission_required('core.manage_infertility_case')
def infertility_case_edit(request, pk):
    case = get_object_or_404(InfertilityCase.objects.select_related('patient'), pk=pk)
    if request.method == 'POST':
        p = request.POST
        case.partner_name = p.get('partner_name', '').strip()
        case.partner_contact = p.get('partner_contact', '').strip()
        case.duration_years = p.get('duration_years') or None
        case.status = p.get('status', case.status)
        case.menstrual_history = p.get('menstrual_history', '').strip()
        case.tubal_factor = p.get('tubal_factor') == '1' if p.get('tubal_factor') else None
        case.ovulatory_factor = p.get('ovulatory_factor') == '1' if p.get('ovulatory_factor') else None
        case.uterine_factor = p.get('uterine_factor') == '1' if p.get('uterine_factor') else None
        case.male_factor = p.get('male_factor') == '1' if p.get('male_factor') else None
        case.semen_analysis = p.get('semen_analysis', '').strip()
        case.hsg_result = p.get('hsg_result', '').strip()
        case.hormone_profile = p.get('hormone_profile', '').strip()
        case.ultrasound_findings = p.get('ultrasound_findings', '').strip()
        case.treatment_plan = p.get('treatment_plan', '').strip()
        case.ovulation_induction = bool(p.get('ovulation_induction'))
        case.iui_done = bool(p.get('iui_done'))
        case.notes = p.get('notes', '').strip()
        case.save()
        messages.success(request, 'Infertility case updated.')
        return redirect('infertility_case_list')

    return render(request, 'obgyn/infertility_form.html', {
        'case': case,
        'selected_patient': case.patient,
        'type_choices': InfertilityCase.Type.choices,
        'status_choices': InfertilityCase.Status.choices,
        'action': 'Edit',
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_obgyn_reports')
def obgyn_reports(request):
    today = date.today()
    date_from = request.GET.get('date_from', (today.replace(day=1)).isoformat())
    date_to = request.GET.get('date_to', today.isoformat())
    report = request.GET.get('report', 'anc_summary')

    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        d_from = today.replace(day=1)
        d_to = today

    data = {}

    if report == 'anc_summary':
        data['anc_visits'] = ANCVisit.objects.filter(
            visit_date__gte=d_from, visit_date__lte=d_to
        ).select_related('pregnancy__patient', 'seen_by').order_by('-visit_date')

    elif report == 'delivery_summary':
        data['deliveries'] = DeliveryRecord.objects.filter(
            delivery_date__gte=d_from, delivery_date__lte=d_to
        ).select_related('pregnancy__patient', 'attended_by').order_by('-delivery_date')
        data['mode_counts'] = (
            DeliveryRecord.objects.filter(delivery_date__gte=d_from, delivery_date__lte=d_to)
            .values('delivery_mode').annotate(count=Count('id')).order_by('-count')
        )

    elif report == 'gyn_summary':
        data['consults'] = GynecologyConsultation.objects.filter(
            consultation_date__gte=d_from, consultation_date__lte=d_to
        ).select_related('patient', 'seen_by').order_by('-consultation_date')
        data['category_counts'] = (
            GynecologyConsultation.objects.filter(
                consultation_date__gte=d_from, consultation_date__lte=d_to
            ).values('category').annotate(count=Count('id')).order_by('-count')
        )

    elif report == 'fp_summary':
        data['fp_visits'] = FamilyPlanningVisit.objects.filter(
            visit_date__gte=d_from, visit_date__lte=d_to
        ).select_related('patient', 'seen_by').order_by('-visit_date')
        data['method_counts'] = (
            FamilyPlanningVisit.objects.filter(visit_date__gte=d_from, visit_date__lte=d_to)
            .values('method').annotate(count=Count('id')).order_by('-count')
        )

    return render(request, 'obgyn/reports.html', {
        'date_from': date_from,
        'date_to': date_to,
        'report': report,
        'data': data,
        'mode_display': dict(DeliveryRecord.DeliveryMode.choices),
        'category_display': dict(GynecologyConsultation.Category.choices),
        'method_display': dict(FamilyPlanningVisit.Method.choices),
    })
