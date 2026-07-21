import secrets
from datetime import date, time as dt_time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, DeathCertificate, MedicalCertificate, Visit

User = get_user_model()


def _doctors():
    return User.objects.filter(
        Q(groups__name__in=['Doctor', 'Ward Doctor', 'Emergency Doctor', 'Surgeon', 'Medical Director'])
    ).distinct().order_by('last_name', 'first_name')


# ── Small POST parsing helpers (mirrors views_surgery.py's _pdate/_pint) ──────

def _pstr(post, name):
    return post.get(name, '').strip()

def _pint(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None

def _pdate(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None

def _ptime(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        return dt_time.fromisoformat(v)
    except ValueError:
        return None


# ── Shared certificate lifecycle helpers ───────────────────────────────────────

def _finalize_certificate(doc, user, prefix):
    """Assign the permanent certificate number + verification code and stamp
    the signing physician. Numbering uses the document's own pk (it was
    already saved as a draft), same 'reuse the row's own id' approach as
    SurgeryOrder.order_number, since re-querying MAX(id) here would just
    find this same draft row."""
    doc.certificate_number = f"{prefix}-{timezone.localdate():%Y%m}-{doc.pk:05d}"
    doc.verification_code = secrets.token_hex(4).upper()
    doc.doc_status = doc.DocStatus.FINALIZED
    doc.finalized_by = user
    doc.finalized_at = timezone.now()
    doc.signature_name = user.get_full_name() or user.username
    doc.save()


def _revise_certificate(new_doc, existing, user, reason, field_names):
    """Create a new draft version of a finalized certificate, copying its
    field values forward. The old row stops being current but is never
    mutated again — same immutable-history pattern as periop documents."""
    existing.is_current = False
    existing.save(update_fields=['is_current'])

    for f in field_names:
        setattr(new_doc, f, getattr(existing, f))
    new_doc.version           = existing.version + 1
    new_doc.supersedes        = existing
    new_doc.is_current        = True
    new_doc.doc_status        = new_doc.DocStatus.DRAFT
    new_doc.revision_reason   = reason
    new_doc.created_by        = user
    new_doc.certificate_number = ''
    new_doc.verification_code  = ''
    new_doc.finalized_by      = None
    new_doc.finalized_at      = None
    new_doc.signature_name    = ''
    new_doc.save()
    return new_doc


def _void_certificate(doc, user, reason):
    doc.is_void     = True
    doc.voided_by   = user
    doc.voided_at   = timezone.now()
    doc.void_reason = reason
    doc.save(update_fields=['is_void', 'voided_by', 'voided_at', 'void_reason'])


# ── Combined dashboard ──────────────────────────────────────────────────────

@hms_permission_required('core.read_medical_certificate')
def certificate_dashboard(request):
    mc_q = _pstr(request.GET, 'mc_q') if request.method == 'GET' else ''
    dc_q = _pstr(request.GET, 'dc_q') if request.method == 'GET' else ''

    medical_certs = MedicalCertificate.objects.filter(is_current=True).select_related('patient', 'visit')
    if mc_q:
        medical_certs = medical_certs.filter(
            Q(certificate_number__icontains=mc_q) |
            Q(patient__first_name__icontains=mc_q) |
            Q(patient__last_name__icontains=mc_q) |
            Q(patient__card_number__icontains=mc_q)
        )
    medical_certs = medical_certs.order_by('-created_at')[:25]

    death_certs = DeathCertificate.objects.filter(is_current=True).select_related('patient', 'visit')
    if dc_q:
        death_certs = death_certs.filter(
            Q(certificate_number__icontains=dc_q) |
            Q(patient__first_name__icontains=dc_q) |
            Q(patient__last_name__icontains=dc_q) |
            Q(patient__card_number__icontains=dc_q)
        )
    death_certs = death_certs.order_by('-created_at')[:25]

    today = timezone.localdate()
    return render(request, 'certificates/dashboard.html', {
        'medical_certs': medical_certs,
        'death_certs': death_certs,
        'mc_q': mc_q,
        'dc_q': dc_q,
        'mc_total': MedicalCertificate.objects.filter(is_current=True).count(),
        'mc_finalized_month': MedicalCertificate.objects.filter(
            doc_status=MedicalCertificate.DocStatus.FINALIZED,
            finalized_at__year=today.year, finalized_at__month=today.month,
        ).count(),
        'dc_total': DeathCertificate.objects.filter(is_current=True).count(),
        'dc_finalized_month': DeathCertificate.objects.filter(
            doc_status=DeathCertificate.DocStatus.FINALIZED,
            finalized_at__year=today.year, finalized_at__month=today.month,
        ).count(),
    })


@hms_permission_required('core.read_medical_certificate')
def certificate_verify(request):
    code = _pstr(request.GET, 'code')
    result = None
    doc_type = None
    if code:
        result = MedicalCertificate.objects.select_related('patient', 'finalized_by').filter(verification_code__iexact=code).first()
        doc_type = 'Medical Certificate'
        if not result:
            result = DeathCertificate.objects.select_related('patient', 'finalized_by').filter(verification_code__iexact=code).first()
            doc_type = 'Death Certificate'
    return render(request, 'certificates/verify.html', {'code': code, 'result': result, 'doc_type': doc_type})


# ── Medical Certificate ──────────────────────────────────────────────────────

MC_FIELDS = [
    'date_of_examination', 'medical_findings', 'diagnosis',
    'rest_from', 'rest_to', 'days_off',
    'fitness_status', 'work_restrictions', 'follow_up_date', 'remarks',
]


def _apply_mc_form(cert, p):
    cert.date_of_examination = _pdate(p, 'date_of_examination') or timezone.localdate()
    cert.medical_findings    = _pstr(p, 'medical_findings')
    cert.diagnosis           = _pstr(p, 'diagnosis')
    cert.rest_from           = _pdate(p, 'rest_from')
    cert.rest_to             = _pdate(p, 'rest_to')
    days_off = _pint(p, 'days_off')
    if days_off is None and cert.rest_from and cert.rest_to and cert.rest_to >= cert.rest_from:
        days_off = (cert.rest_to - cert.rest_from).days + 1
    cert.days_off            = days_off
    cert.fitness_status      = p.get('fitness_status', MedicalCertificate.Fitness.FIT_WORK_SCHOOL)
    cert.work_restrictions   = _pstr(p, 'work_restrictions')
    cert.follow_up_date      = _pdate(p, 'follow_up_date')
    cert.remarks             = _pstr(p, 'remarks')


@hms_permission_required('core.write_medical_certificate')
def medical_certificate_create(request, visit_id):
    visit = get_object_or_404(Visit.objects.select_related('patient', 'doctor', 'department'), pk=visit_id)
    existing = visit.medical_certificates.filter(is_current=True).first()

    if request.method == 'POST':
        p = request.POST
        try:
            with transaction.atomic():
                if existing and existing.is_finalized:
                    reason = _pstr(p, 'revision_reason')
                    if not reason:
                        messages.error(request, 'A reason is required to revise a finalized medical certificate.')
                        return redirect('medical_certificate_create', visit_id=visit_id)
                    cert = _revise_certificate(
                        MedicalCertificate(visit=visit, patient=visit.patient),
                        existing, request.user, reason, MC_FIELDS,
                    )
                    is_new = True
                elif existing:
                    cert = existing
                    is_new = False
                else:
                    cert = MedicalCertificate(visit=visit, patient=visit.patient, created_by=request.user)
                    is_new = False

                _apply_mc_form(cert, p)
                cert.updated_by = request.user
                cert.save()

            log_action(
                request.user, AuditLog.Action.CREATE if is_new or not existing else AuditLog.Action.UPDATE,
                AuditLog.Module.MEDICAL_CERTIFICATE,
                object_type='MedicalCertificate', object_id=cert.pk,
                object_repr=f'{visit.patient.full_name} v{cert.version}',
                description=f'Medical certificate {"revised (new version)" if is_new and existing else "saved"} for {visit.patient.full_name}',
                request=request,
            )
            messages.success(request, 'Medical certificate saved as draft.')
            return redirect('visit_detail', visit_id=visit_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'certificates/medical_certificate_form.html', {
        'visit': visit,
        'cert': existing,
        'fitness_choices': MedicalCertificate.Fitness.choices,
    })


@require_POST
@hms_permission_required('core.finalize_medical_certificate')
def medical_certificate_finalize(request, certificate_id):
    cert = get_object_or_404(MedicalCertificate, pk=certificate_id)
    if cert.is_finalized:
        messages.error(request, 'This medical certificate is already finalized.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    if not request.POST.get('certify'):
        messages.error(request, 'You must certify the record is accurate and complete to finalize.')
        return redirect('medical_certificate_create', visit_id=cert.visit_id)
    _finalize_certificate(cert, request.user, 'MC')
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.MEDICAL_CERTIFICATE,
        object_type='MedicalCertificate', object_id=cert.pk, object_repr=cert.certificate_number,
        description=f'Medical certificate {cert.certificate_number} finalized & signed',
        request=request,
    )
    messages.success(request, 'Medical certificate finalized and signed.')
    return redirect('visit_detail', visit_id=cert.visit_id)


@require_POST
@hms_permission_required('core.void_medical_certificate')
def medical_certificate_void(request, certificate_id):
    cert = get_object_or_404(MedicalCertificate, pk=certificate_id)
    if not cert.is_finalized or cert.is_void:
        messages.error(request, 'Only a finalized, non-voided certificate can be voided.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    reason = _pstr(request.POST, 'void_reason')
    if not reason:
        messages.error(request, 'A reason is required to void a certificate.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    _void_certificate(cert, request.user, reason)
    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.MEDICAL_CERTIFICATE,
        object_type='MedicalCertificate', object_id=cert.pk, object_repr=cert.certificate_number,
        description=f'Medical certificate {cert.certificate_number} voided — {reason}',
        request=request,
    )
    messages.success(request, 'Medical certificate voided.')
    return redirect('visit_detail', visit_id=cert.visit_id)


@hms_permission_required('core.read_medical_certificate')
def medical_certificate_history(request, visit_id):
    visit = get_object_or_404(Visit, pk=visit_id)
    versions = visit.medical_certificates.select_related('created_by', 'finalized_by').order_by('-version')
    return render(request, 'certificates/medical_certificate_history.html', {'visit': visit, 'versions': versions})


@hms_permission_required('core.read_medical_certificate')
def medical_certificate_print(request, certificate_id):
    cert = get_object_or_404(
        MedicalCertificate.objects.select_related('visit__patient', 'visit__doctor', 'visit__department', 'finalized_by', 'created_by'),
        pk=certificate_id,
    )
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.MEDICAL_CERTIFICATE,
        object_type='MedicalCertificate', object_id=cert.pk,
        object_repr=cert.certificate_number or f'v{cert.version} (draft)',
        description=f'Printed medical certificate for {cert.patient.full_name}',
        request=request,
    )
    return render(request, 'certificates/medical_certificate_print.html', {'cert': cert, 'now': timezone.now()})


# ── Death Certificate ────────────────────────────────────────────────────────

DC_FIELDS = [
    'date_of_death', 'time_of_death', 'place_of_death', 'ward', 'room', 'bed',
    'immediate_cause', 'underlying_cause', 'contributing_conditions',
    'manner_of_death', 'duration_of_illness', 'attending_physician', 'clinical_notes',
]


def _apply_dc_form(cert, p):
    cert.date_of_death  = _pdate(p, 'date_of_death') or timezone.localdate()
    cert.time_of_death  = _ptime(p, 'time_of_death') or timezone.localtime().time()
    cert.place_of_death = _pstr(p, 'place_of_death')
    cert.ward            = _pstr(p, 'ward')
    cert.room            = _pstr(p, 'room')
    cert.bed             = _pstr(p, 'bed')
    cert.immediate_cause         = _pstr(p, 'immediate_cause')
    cert.underlying_cause         = _pstr(p, 'underlying_cause')
    cert.contributing_conditions = _pstr(p, 'contributing_conditions')
    cert.manner_of_death          = p.get('manner_of_death', DeathCertificate.MannerOfDeath.NATURAL)
    cert.duration_of_illness      = _pstr(p, 'duration_of_illness')
    attending_id = _pint(p, 'attending_physician')
    cert.attending_physician_id = attending_id
    cert.clinical_notes = _pstr(p, 'clinical_notes')


@hms_permission_required('core.write_death_certificate')
def death_certificate_create(request, visit_id):
    visit = get_object_or_404(Visit.objects.select_related('patient', 'doctor', 'department'), pk=visit_id)
    existing = visit.death_certificates.filter(is_current=True).first()
    admission = visit.admissions.select_related('bed__room__ward').order_by('-admitted_at').first()
    discharge_summary = getattr(visit, 'discharge_summary', None)

    if request.method == 'POST':
        p = request.POST
        try:
            with transaction.atomic():
                if existing and existing.is_finalized:
                    reason = _pstr(p, 'revision_reason')
                    if not reason:
                        messages.error(request, 'A reason is required to revise a finalized death certificate.')
                        return redirect('death_certificate_create', visit_id=visit_id)
                    cert = _revise_certificate(
                        DeathCertificate(
                            visit=visit, patient=visit.patient,
                            admission=existing.admission, discharge_summary=existing.discharge_summary,
                        ),
                        existing, request.user, reason, DC_FIELDS,
                    )
                    is_new = True
                elif existing:
                    cert = existing
                    is_new = False
                else:
                    cert = DeathCertificate(
                        visit=visit, patient=visit.patient, created_by=request.user,
                        admission=admission, discharge_summary=discharge_summary,
                        attending_physician=getattr(visit.doctor, 'user', None),
                    )
                    is_new = False

                _apply_dc_form(cert, p)
                cert.updated_by = request.user
                cert.save()

            log_action(
                request.user, AuditLog.Action.CREATE if is_new or not existing else AuditLog.Action.UPDATE,
                AuditLog.Module.DEATH_CERTIFICATE,
                object_type='DeathCertificate', object_id=cert.pk,
                object_repr=f'{visit.patient.full_name} v{cert.version}',
                description=f'Death certificate {"revised (new version)" if is_new and existing else "saved"} for {visit.patient.full_name}',
                request=request,
            )
            messages.success(request, 'Death certificate saved as draft.')
            return redirect('visit_detail', visit_id=visit_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    prefill_ward = existing.ward if existing else (admission.bed.room.ward.name if admission else '')
    prefill_room = existing.room if existing else (admission.bed.room.room_number if admission else '')
    prefill_bed  = existing.bed if existing else (admission.bed.bed_code if admission else '')
    prefill_place = existing.place_of_death if existing else (
        f'{admission.bed.room.ward.name} — {admission.bed.bed_code}' if admission else visit.department.name
    )

    return render(request, 'certificates/death_certificate_form.html', {
        'visit': visit,
        'cert': existing,
        'admission': admission,
        'manner_choices': DeathCertificate.MannerOfDeath.choices,
        'doctors': _doctors(),
        'prefill_ward': prefill_ward,
        'prefill_room': prefill_room,
        'prefill_bed': prefill_bed,
        'prefill_place': prefill_place,
    })


@require_POST
@hms_permission_required('core.finalize_death_certificate')
def death_certificate_finalize(request, certificate_id):
    cert = get_object_or_404(DeathCertificate, pk=certificate_id)
    if cert.is_finalized:
        messages.error(request, 'This death certificate is already finalized.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    if not request.POST.get('certify'):
        messages.error(request, 'You must certify the record is accurate and complete to finalize.')
        return redirect('death_certificate_create', visit_id=cert.visit_id)
    _finalize_certificate(cert, request.user, 'DC')
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.DEATH_CERTIFICATE,
        object_type='DeathCertificate', object_id=cert.pk, object_repr=cert.certificate_number,
        description=f'Death certificate {cert.certificate_number} finalized & signed',
        request=request,
    )
    messages.success(request, 'Death certificate finalized and signed.')
    return redirect('visit_detail', visit_id=cert.visit_id)


@require_POST
@hms_permission_required('core.void_death_certificate')
def death_certificate_void(request, certificate_id):
    cert = get_object_or_404(DeathCertificate, pk=certificate_id)
    if not cert.is_finalized or cert.is_void:
        messages.error(request, 'Only a finalized, non-voided certificate can be voided.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    reason = _pstr(request.POST, 'void_reason')
    if not reason:
        messages.error(request, 'A reason is required to void a certificate.')
        return redirect('visit_detail', visit_id=cert.visit_id)
    _void_certificate(cert, request.user, reason)
    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.DEATH_CERTIFICATE,
        object_type='DeathCertificate', object_id=cert.pk, object_repr=cert.certificate_number,
        description=f'Death certificate {cert.certificate_number} voided — {reason}',
        request=request,
    )
    messages.success(request, 'Death certificate voided.')
    return redirect('visit_detail', visit_id=cert.visit_id)


@hms_permission_required('core.read_death_certificate')
def death_certificate_history(request, visit_id):
    visit = get_object_or_404(Visit, pk=visit_id)
    versions = visit.death_certificates.select_related('created_by', 'finalized_by').order_by('-version')
    return render(request, 'certificates/death_certificate_history.html', {'visit': visit, 'versions': versions})


@hms_permission_required('core.read_death_certificate')
def death_certificate_print(request, certificate_id):
    cert = get_object_or_404(
        DeathCertificate.objects.select_related(
            'visit__patient', 'visit__doctor', 'visit__department',
            'finalized_by', 'created_by', 'attending_physician', 'admission__bed__room__ward',
        ),
        pk=certificate_id,
    )
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.DEATH_CERTIFICATE,
        object_type='DeathCertificate', object_id=cert.pk,
        object_repr=cert.certificate_number or f'v{cert.version} (draft)',
        description=f'Printed death certificate for {cert.patient.full_name}',
        request=request,
    )
    return render(request, 'certificates/death_certificate_print.html', {'cert': cert, 'now': timezone.now()})
