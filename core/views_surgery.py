import copy
from datetime import date, datetime as dt, timedelta

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import build_changes, log_action
from .decorators import hms_permission_required
from .models import (
    AdmissionRequest, Admission, AuditLog, Department, Invoice, InventoryItem, InventoryTransaction,
    InvoiceItem, InpatientDepositAccount, OperativeNote, ORRoom, Patient, Payment,
    PeriopNursingAddendum, PostOperativeNote, ProcedureCategory, ProcedureMaster,
    SurgeryAnesthesiaRecord, SurgeryConsumable, SurgeryOrder, SurgeryPACURecord,
    SurgeryPreOpChecklist, SurgeryPreDeposit, SurgerySchedule,
    VitalSign, Visit, Ward,
)
from .report_export import export_excel

User = get_user_model()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _surgeons():
    return User.objects.filter(
        Q(groups__name__in=['Surgeon', 'Doctor', 'Ward Doctor', 'Emergency Doctor', 'Medical Director'])
    ).distinct().order_by('last_name', 'first_name')

def _anesthesiologists():
    return User.objects.filter(groups__name='Anesthesia Team').order_by('last_name', 'first_name')

def _nurses():
    return User.objects.filter(
        Q(groups__name__in=['OR Nurse', 'Nurse', 'Ward Nurse'])
    ).distinct().order_by('last_name', 'first_name')


# ── Perioperative Documentation Helpers ────────────────────────────────────────

def _periop_header_context(order):
    """Single source of truth for perioperative form/print auto-fill values —
    called by every create/edit/print view for the three documents so the
    auto-fill logic exists exactly once."""
    schedule = getattr(order, 'schedule', None)
    latest_vitals = None
    if order.visit_id:
        latest_vitals = VitalSign.objects.filter(visit_id=order.visit_id).order_by('-recorded_at').first()
    # A plain dict with guaranteed keys (even if the value is None) — this
    # lets templates use `header.vitals.bp_systolic` as a filter argument
    # (e.g. `|default_if_none:header.vitals.bp_systolic`) without Django
    # raising VariableDoesNotExist, which happens if the base object itself
    # (latest_vitals) is None rather than just one of its attributes.
    vitals = {
        'bp_systolic': latest_vitals.bp_systolic if latest_vitals else None,
        'bp_diastolic': latest_vitals.bp_diastolic if latest_vitals else None,
        'pulse': latest_vitals.pulse if latest_vitals else None,
        'respiratory_rate': latest_vitals.respiratory_rate if latest_vitals else None,
        'temperature': latest_vitals.temperature if latest_vitals else None,
        'spo2': latest_vitals.spo2 if latest_vitals else None,
        'weight': latest_vitals.weight if latest_vitals else None,
        'height': latest_vitals.height if latest_vitals else None,
    }
    return {
        'patient':            order.patient,
        'age':                order.patient.age_display,
        'department':         order.department,
        'ward_room':          schedule.or_room.name if schedule else (order.department.name if order.department else ''),
        'surgeon':            order.surgeon,
        'assistant_surgeon':  order.assistant_surgeon,
        'planned_procedure':  order.planned_procedure,
        'scheduled_date':     schedule.scheduled_date if schedule else order.planned_date,
        'or_room':            schedule.or_room if schedule else None,
        'scrub_nurse':        schedule.scrub_nurse if schedule else None,
        'circulating_nurse':  schedule.circulating_nurse if schedule else None,
        'schedule_anesthesiologist': schedule.anesthesiologist if schedule else None,
        'latest_vitals':      latest_vitals,
        'vitals':             vitals,
        'anesthesia_type':    order.anesthesia_type,
    }


def _revise_periop_document(model_cls, existing, user, reason, field_names, m2m_field_names=()):
    """Create a new version of a finalized perioperative document, copying its
    field values as the starting point for further edits. The old row is
    never mutated again (is_current flips to False) — this is the entire
    mechanism behind "no finalized documentation is ever deleted or
    overwritten; corrections create a new revision."""
    # The old row must stop being "current" BEFORE the new row is saved as
    # current — both rows briefly having is_current=True at once would
    # violate the partial unique constraint (one current row per order).
    existing.is_current = False
    existing.save(update_fields=['is_current'])

    new_doc = model_cls(surgery_order=existing.surgery_order)
    for f in field_names:
        setattr(new_doc, f, getattr(existing, f))
    new_doc.version          = existing.version + 1
    new_doc.supersedes       = existing
    new_doc.is_current       = True
    new_doc.doc_status       = model_cls.DocStatus.DRAFT
    new_doc.revision_reason  = reason
    new_doc.created_by       = user
    new_doc.finalized_by     = None
    new_doc.finalized_at     = None
    new_doc.signature_name   = ''
    new_doc.save()
    for m2m_name in m2m_field_names:
        getattr(new_doc, m2m_name).set(getattr(existing, m2m_name).all())
    return new_doc


def _finalize_periop_document(doc, user):
    doc.doc_status     = doc.DocStatus.FINALIZED
    doc.finalized_by   = user
    doc.finalized_at    = timezone.now()
    doc.signature_name  = user.get_full_name() or user.username
    doc.save()


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

def _pdec(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        return None

def _pdatetime(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        parsed = dt.fromisoformat(v)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed

def _pdate(post, name):
    v = post.get(name, '').strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None

def _pbool(post, name):
    return post.get(name) in ('on', 'true', '1', 'True')


# ── Surgery Dashboard ─────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_dashboard(request):
    today = timezone.localdate()

    orders = SurgeryOrder.objects.select_related('patient', 'surgeon', 'department', 'procedure_master')

    # ── KPIs ──────────────────────────────────────────────────────────────────
    total_orders     = orders.count()
    pending          = orders.filter(status__in=['ordered', 'pending_review']).count()
    approved         = orders.filter(status='approved').count()
    scheduled_today  = SurgerySchedule.objects.filter(scheduled_date=today).count()
    in_or            = orders.filter(status='in_or').count()
    recovery         = orders.filter(status='recovery').count()
    post_op_ward     = orders.filter(status='post_op').count()
    completed_month  = orders.filter(
        status='completed',
        completed_at__year=today.year,
        completed_at__month=today.month,
    ).count()
    cancelled_month  = orders.filter(
        cancelled_at__year=today.year,
        cancelled_at__month=today.month,
    ).count()

    # ── Pipeline buckets (for Kanban-style command center) ─────────────────────
    pipeline_ordered = orders.filter(status__in=['ordered', 'pending_review']).order_by('-ordered_at')[:8]
    pipeline_approved = orders.filter(status__in=['approved', 'awaiting_decision', 'booking_deposit']).order_by('-ordered_at')[:8]
    pipeline_scheduled = (
        SurgerySchedule.objects
        .filter(scheduled_date__gte=today)
        .select_related('surgery_order__patient', 'surgery_order__surgeon',
                        'surgery_order__procedure_master', 'or_room')
        .order_by('scheduled_date', 'scheduled_start_time')[:10]
    )
    pipeline_admission = orders.filter(status='awaiting_admission').select_related('patient')[:8]
    pipeline_in_or     = orders.filter(status='in_or').select_related('patient', 'surgeon')[:6]
    pipeline_recovery  = orders.filter(status='recovery').select_related('patient')[:6]
    pipeline_ward      = orders.filter(status='post_op').select_related('patient', 'surgeon')[:8]

    # ── Today's schedule ───────────────────────────────────────────────────────
    todays_schedule = (
        SurgerySchedule.objects
        .filter(scheduled_date=today)
        .select_related('surgery_order__patient', 'surgery_order__surgeon',
                        'surgery_order__procedure_master', 'or_room')
        .order_by('scheduled_start_time')
    )

    # ── Urgent / Emergency needing attention ───────────────────────────────────
    urgent = orders.filter(
        priority__in=['emergency', 'urgent'],
        status__in=['ordered', 'pending_review', 'approved'],
    ).order_by('priority', '-ordered_at')[:10]

    # ── Recent orders ──────────────────────────────────────────────────────────
    recent = orders.order_by('-ordered_at')[:8]

    # ── OR rooms with today's load ─────────────────────────────────────────────
    or_rooms = ORRoom.objects.filter(is_active=True).order_by('name')

    return render(request, 'surgery/dashboard.html', {
        'total_orders':       total_orders,
        'pending':            pending,
        'approved':           approved,
        'scheduled_today':    scheduled_today,
        'in_or':              in_or,
        'recovery':           recovery,
        'post_op_ward':       post_op_ward,
        'completed_month':    completed_month,
        'cancelled_month':    cancelled_month,
        'pipeline_ordered':   pipeline_ordered,
        'pipeline_approved':  pipeline_approved,
        'pipeline_scheduled': pipeline_scheduled,
        'pipeline_admission': pipeline_admission,
        'pipeline_in_or':     pipeline_in_or,
        'pipeline_recovery':  pipeline_recovery,
        'pipeline_ward':      pipeline_ward,
        'todays_schedule':    todays_schedule,
        'urgent':             urgent,
        'recent':             recent,
        'today':              today,
        'or_rooms':           or_rooms,
    })


# ── Surgery Order List ────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_order_list(request):
    qs = SurgeryOrder.objects.select_related('patient', 'surgeon', 'department', 'procedure_master')

    status_f   = request.GET.get('status', '').strip()
    priority_f = request.GET.get('priority', '').strip()
    dept_f     = request.GET.get('dept', '').strip()
    q          = request.GET.get('q', '').strip()
    date_from  = request.GET.get('date_from', '').strip()
    date_to    = request.GET.get('date_to', '').strip()

    if status_f:
        qs = qs.filter(status=status_f)
    if priority_f:
        qs = qs.filter(priority=priority_f)
    if dept_f:
        qs = qs.filter(department_id=dept_f)
    if q:
        qs = qs.filter(
            Q(order_number__icontains=q) |
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q) |
            Q(patient__card_number__icontains=q) |
            Q(planned_procedure__icontains=q)
        )
    if date_from:
        qs = qs.filter(ordered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(ordered_at__date__lte=date_to)

    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'surgery/order_list.html', {
        'page_obj':   page_obj,
        'statuses':   SurgeryOrder.Status.choices,
        'priorities': SurgeryOrder.Priority.choices,
        'departments': Department.objects.filter(is_active=True),
        'status_f':   status_f,
        'priority_f': priority_f,
        'dept_f':     dept_f,
        'q':          q,
        'date_from':  date_from,
        'date_to':    date_to,
    })


# ── Surgery Order Create ──────────────────────────────────────────────────────

@hms_permission_required('core.order_surgery')
def surgery_order_create(request, patient_id=None, visit_id=None):
    patient = get_object_or_404(Patient, pk=patient_id) if patient_id else None
    visit   = get_object_or_404(Visit, pk=visit_id) if visit_id else None
    if visit and not patient:
        patient = visit.patient

    procedures  = ProcedureMaster.objects.filter(is_active=True).order_by('name')
    surgeons    = _surgeons()
    departments = Department.objects.filter(is_active=True)
    patients    = Patient.objects.filter(is_active=True).order_by('last_name', 'first_name') if not patient else None

    if request.method == 'POST':
        p = request.POST
        patient_id_post = p.get('patient') or (patient.pk if patient else None)
        if not patient_id_post:
            messages.error(request, 'Patient is required.')
            return _order_form_render(request, procedures, surgeons, departments, patients, patient, visit, p)

        pt = get_object_or_404(Patient, pk=patient_id_post)
        visit_id_post = p.get('visit') or (visit.pk if visit else None)
        vt = Visit.objects.filter(pk=visit_id_post).first() if visit_id_post else None

        proc_id = p.get('procedure_master')
        proc    = ProcedureMaster.objects.filter(pk=proc_id, is_active=True).first() if proc_id else None

        surgeon_id = p.get('surgeon')
        if not surgeon_id:
            messages.error(request, 'Surgeon is required.')
            return _order_form_render(request, procedures, surgeons, departments, patients, pt, vt, p)

        def _decimal(val, default='0'):
            try:
                from decimal import Decimal as D, InvalidOperation
                return D(str(val).strip() or default)
            except Exception:
                return D(default)

        order = SurgeryOrder(
            patient          = pt,
            visit            = vt,
            procedure_master = proc,
            status             = SurgeryOrder.Status.AWAITING_DECISION,
            pre_op_diagnosis   = p.get('pre_op_diagnosis', '').strip(),
            planned_procedure  = p.get('planned_procedure', '').strip() or (proc.name if proc else ''),
            indication         = p.get('indication', '').strip(),
            priority           = p.get('priority', SurgeryOrder.Priority.ELECTIVE),
            planned_date       = p.get('planned_date') or None,
            preferred_time     = p.get('preferred_time') or None,
            surgeon_id         = surgeon_id,
            assistant_surgeon_id = p.get('assistant_surgeon') or None,
            department_id      = p.get('department') or None,
            special_instructions = p.get('special_instructions', '').strip(),
            previous_history     = p.get('previous_history', '').strip(),
            required_preparation = p.get('required_preparation', '').strip(),
            anesthesia_type      = p.get('anesthesia_type', SurgeryOrder.AnesthesiaType.GENERAL),
            anesthesia_assessment_requested = bool(p.get('anesthesia_assessment_requested')),
            notes      = p.get('notes', '').strip(),
            ordered_by = request.user,
            estimated_procedure_fee  = _decimal(p.get('estimated_procedure_fee', '0')),
            estimated_surgeon_fee    = _decimal(p.get('estimated_surgeon_fee', '0')),
            estimated_anesthesia_fee = _decimal(p.get('estimated_anesthesia_fee', '0')),
            estimated_facility_fee   = _decimal(p.get('estimated_facility_fee', '0')),
        )
        try:
            order.full_clean()
            order.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                object_type='SurgeryOrder', object_id=order.pk,
                object_repr=order.order_number,
                description=f'Surgery order created and sent to reception for counseling: {order.planned_procedure} for {pt.full_name} ({order.priority})',
                extra_data={'priority': order.priority, 'patient': pt.full_name},
                severity=AuditLog.Severity.WARNING if order.priority == 'emergency' else AuditLog.Severity.INFO,
                request=request,
            )
            messages.success(request, f'Surgery order {order.order_number} created and sent to reception for patient counseling.')
            return redirect('surgery_order_detail', order_id=order.pk)
        except Exception as exc:
            messages.error(request, f'Error saving order: {exc}')

    return _order_form_render(request, procedures, surgeons, departments, patients, patient, visit, {})


def _order_form_render(request, procedures, surgeons, departments, patients, patient, visit, post):
    return render(request, 'surgery/order_form.html', {
        'procedures':   procedures,
        'surgeons':     surgeons,
        'departments':  departments,
        'patients':     patients,
        'patient':      patient,
        'visit':        visit,
        'anesthesia_types': SurgeryOrder.AnesthesiaType.choices,
        'priorities':       SurgeryOrder.Priority.choices,
        'post':         post,
    })


# ── Surgery Order Detail ──────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_order_detail(request, order_id):
    order = get_object_or_404(
        SurgeryOrder.objects.select_related(
            'patient', 'visit', 'surgeon', 'assistant_surgeon',
            'department', 'procedure_master', 'ordered_by',
            'approved_by', 'cancelled_by', 'invoice', 'invoice_item',
            'surgery_admission_request', 'booking_deposit_invoice_item',
            'counseling_done_by', 'patient_decision_by',
        ),
        pk=order_id,
    )

    schedule          = getattr(order, 'schedule', None)
    anesthesia_record = order.current_anesthesia_record
    operative_note    = order.current_operative_note
    postop_note       = order.current_postop_note
    consumables       = order.consumables.select_related('recorded_by').all()
    consumable_total  = consumables.aggregate(t=Sum('total_cost'))['t'] or 0
    nursing_addenda   = order.nursing_addenda.select_related('created_by').all()

    # Pre-op checklist (if exists)
    try:
        preop_checklist = order.preop_checklist
    except SurgeryPreOpChecklist.DoesNotExist:
        preop_checklist = None

    # PACU record
    pacu_record = SurgeryPACURecord.objects.filter(
        surgery_order=order, is_current=True,
    ).first()

    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.SURGERY,
        object_type='SurgeryOrder', object_id=order.pk,
        object_repr=order.order_number,
        description=f'Surgery order viewed: {order.order_number} ({order.patient.full_name})',
        request=request,
    )

    or_items = InventoryItem.objects.filter(
        item_type=InventoryItem.ItemType.SURGICAL, is_active=True,
    ).order_by('name')

    return render(request, 'surgery/order_detail.html', {
        'order':              order,
        'schedule':           schedule,
        'anesthesia_record':  anesthesia_record,
        'operative_note':     operative_note,
        'postop_note':        postop_note,
        'nursing_addenda':    nursing_addenda,
        'nursing_doc_types':  PeriopNursingAddendum.DocumentType.choices,
        'consumables':        consumables,
        'consumable_total':   consumable_total,
        'status_choices':     SurgeryOrder.Status.choices,
        'or_items':           or_items,
        'preop_checklist':    preop_checklist,
        'pacu_record':        pacu_record,
    })


# ── Surgery Order Edit ────────────────────────────────────────────────────────

@hms_permission_required('core.order_surgery')
def surgery_order_edit(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    if order.status not in ('ordered', 'pending_review'):
        messages.error(request, 'Only orders in Ordered or Pending Review status can be edited.')
        return redirect('surgery_order_detail', order_id=order_id)

    procedures  = ProcedureMaster.objects.filter(is_active=True).order_by('name')
    surgeons    = _surgeons()
    departments = Department.objects.filter(is_active=True)

    if request.method == 'POST':
        p = request.POST
        old = {
            'planned_procedure': order.planned_procedure,
            'pre_op_diagnosis':  order.pre_op_diagnosis,
            'priority':          order.priority,
            'planned_date':      str(order.planned_date) if order.planned_date else None,
        }
        proc_id = p.get('procedure_master')
        order.procedure_master = ProcedureMaster.objects.filter(pk=proc_id, is_active=True).first() if proc_id else None
        order.pre_op_diagnosis     = p.get('pre_op_diagnosis', '').strip()
        order.planned_procedure    = p.get('planned_procedure', '').strip()
        order.indication           = p.get('indication', '').strip()
        order.priority             = p.get('priority', order.priority)
        order.planned_date         = p.get('planned_date') or None
        order.preferred_time       = p.get('preferred_time') or None
        order.surgeon_id           = p.get('surgeon') or order.surgeon_id
        order.assistant_surgeon_id = p.get('assistant_surgeon') or None
        order.department_id        = p.get('department') or None
        order.special_instructions  = p.get('special_instructions', '').strip()
        order.previous_history      = p.get('previous_history', '').strip()
        order.required_preparation  = p.get('required_preparation', '').strip()
        order.anesthesia_type       = p.get('anesthesia_type', order.anesthesia_type)
        order.anesthesia_assessment_requested = bool(p.get('anesthesia_assessment_requested'))
        order.notes = p.get('notes', '').strip()
        try:
            from decimal import Decimal as D
            def _dec(v, d='0'):
                try: return D(str(v).strip() or d)
                except Exception: return D(d)
            order.estimated_procedure_fee  = _dec(p.get('estimated_procedure_fee', '0'))
            order.estimated_surgeon_fee    = _dec(p.get('estimated_surgeon_fee', '0'))
            order.estimated_anesthesia_fee = _dec(p.get('estimated_anesthesia_fee', '0'))
            order.estimated_facility_fee   = _dec(p.get('estimated_facility_fee', '0'))
        except Exception:
            pass
        try:
            order.save()
            new = {
                'planned_procedure': order.planned_procedure,
                'pre_op_diagnosis':  order.pre_op_diagnosis,
                'priority':          order.priority,
                'planned_date':      str(order.planned_date) if order.planned_date else None,
            }
            changes = {k: {'old': old[k], 'new': new[k]} for k in old if old[k] != new[k]}
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                object_type='SurgeryOrder', object_id=order.pk,
                object_repr=order.order_number,
                description=f'Surgery order updated: {order.order_number}',
                changes=changes or None,
                request=request,
            )
            messages.success(request, 'Surgery order updated.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/order_form.html', {
        'order':        order,
        'procedures':   procedures,
        'surgeons':     surgeons,
        'departments':  departments,
        'patients':     None,
        'patient':      order.patient,
        'visit':        order.visit,
        'anesthesia_types': SurgeryOrder.AnesthesiaType.choices,
        'priorities':       SurgeryOrder.Priority.choices,
        'post':         {},
        'edit_mode':    True,
    })


# ── Surgery Order Status Update ───────────────────────────────────────────────

@hms_permission_required('core.approve_surgery_order')
@require_POST
def surgery_order_update_status(request, order_id):
    order  = get_object_or_404(SurgeryOrder, pk=order_id)
    action = request.POST.get('action', '').strip()
    now    = timezone.now()

    if action == 'approve' and order.can_approve:
        order.status      = SurgeryOrder.Status.APPROVED
        order.approved_by = request.user
        order.approved_at = now
        order.save()
        log_action(
            request.user, AuditLog.Action.APPROVE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery order approved: {order.order_number} ({order.patient.full_name})',
            request=request,
        )
        messages.success(request, f'Order {order.order_number} approved.')

    elif action == 'cancel' and order.can_cancel:
        reason = request.POST.get('cancellation_reason', '').strip()
        order.status              = SurgeryOrder.Status.CANCELLED
        order.cancelled_by        = request.user
        order.cancelled_at        = now
        order.cancellation_reason = reason
        order.save()
        log_action(
            request.user, AuditLog.Action.CANCEL, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery order cancelled: {order.order_number}. Reason: {reason or "—"}',
            severity=AuditLog.Severity.WARNING,
            request=request,
        )
        messages.warning(request, f'Order {order.order_number} cancelled.')

    elif action == 'mark_credit_and_in_or' and order.status == SurgeryOrder.Status.PATIENT_PREPARED:
        if not (request.user.has_perm('core.approve_surgery_order') or request.user.has_perm('core.manage_or_schedule')):
            messages.error(request, 'You do not have permission to override the payment gate.')
            return redirect('surgery_order_detail', order_id=order_id)
        order.payment_status = SurgeryOrder.PaymentStatus.CREDIT
        order.status = SurgeryOrder.Status.IN_OR
        order.save()
        if hasattr(order, 'schedule') and not order.schedule.actual_start_time:
            order.schedule.actual_start_time = now
            order.schedule.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery started with credit payment override: {order.order_number}',
            severity=AuditLog.Severity.WARNING,
            request=request,
        )
        messages.warning(request, 'Payment marked as Credit — patient moved to OR. Ensure billing is settled post-surgery.')

    elif action == 'in_or' and order.status == SurgeryOrder.Status.PATIENT_PREPARED:
        order.status = SurgeryOrder.Status.IN_OR
        order.save()
        if hasattr(order, 'schedule') and not order.schedule.actual_start_time:
            order.schedule.actual_start_time = now
            order.schedule.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery started (In OR): {order.order_number}',
            severity=AuditLog.Severity.WARNING,
            request=request,
        )
        messages.success(request, 'Surgery started — patient in OR.')

    elif action == 'complete' and order.status == SurgeryOrder.Status.IN_OR:
        order.status       = SurgeryOrder.Status.COMPLETED
        order.completed_at = now
        order.save()
        if hasattr(order, 'schedule') and not order.schedule.actual_end_time:
            order.schedule.actual_end_time = now
            order.schedule.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery completed: {order.order_number} ({order.patient.full_name})',
            request=request,
        )
        messages.success(request, f'Surgery {order.order_number} marked as completed.')

    elif action == 'post_op' and order.status in (
        SurgeryOrder.Status.COMPLETED, SurgeryOrder.Status.RECOVERY,
    ):
        order.status = SurgeryOrder.Status.POST_OP
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery order moved to post-operative care: {order.order_number}',
            request=request,
        )
        messages.success(request, 'Order moved to post-operative care.')

    elif action == 'send_to_reception' and order.can_send_to_reception:
        order.status = SurgeryOrder.Status.AWAITING_DECISION
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgery order sent to reception for patient counseling: {order.order_number}',
            request=request,
        )
        messages.success(request, 'Order sent to reception — awaiting patient decision.')

    elif action == 'recovery' and order.status == SurgeryOrder.Status.COMPLETED:
        order.status      = SurgeryOrder.Status.RECOVERY
        order.recovered_at = now
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Patient moved to recovery room: {order.order_number}',
            request=request,
        )
        messages.success(request, 'Patient moved to recovery room.')

    elif action == 'discharge_surgery' and order.status == SurgeryOrder.Status.POST_OP:
        order.status              = SurgeryOrder.Status.DISCHARGED
        order.discharged_surgery_at = now
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Surgical patient discharged: {order.order_number} ({order.patient.full_name})',
            severity=AuditLog.Severity.WARNING,
            request=request,
        )
        messages.success(request, f'Patient {order.patient.full_name} discharged.')

    elif action == 'patient_prepared' and order.status in (
        SurgeryOrder.Status.SCHEDULED, SurgeryOrder.Status.AWAITING_ADMISSION,
    ):
        order.status = SurgeryOrder.Status.PATIENT_PREPARED
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Patient prepared for surgery: {order.order_number}',
            request=request,
        )
        messages.success(request, 'Patient marked as prepared.')

    else:
        messages.error(request, 'Invalid action for current order status.')

    return redirect('surgery_order_detail', order_id=order_id)


# ── OR Dashboard ──────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_dashboard(request):
    today  = timezone.localdate()
    week_end = today + timedelta(days=7)

    upcoming = (
        SurgerySchedule.objects
        .filter(scheduled_date__gte=today, scheduled_date__lt=week_end)
        .select_related(
            'surgery_order__patient', 'surgery_order__surgeon',
            'surgery_order__procedure_master', 'or_room',
        )
        .order_by('scheduled_date', 'scheduled_start_time')
    )

    rooms       = ORRoom.objects.filter(is_active=True)
    in_or_count = SurgeryOrder.objects.filter(status='in_or').count()
    approved_unscheduled = SurgeryOrder.objects.filter(
        status='approved'
    ).select_related('patient', 'surgeon', 'procedure_master').order_by('planned_date', '-priority')

    return render(request, 'surgery/or_dashboard.html', {
        'upcoming':           upcoming,
        'rooms':              rooms,
        'in_or_count':        in_or_count,
        'approved_unscheduled': approved_unscheduled,
        'today':              today,
        'week_end':           week_end,
    })


# ── OR Schedule Create / Edit ─────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def surgery_schedule_create(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    if order.status not in (SurgeryOrder.Status.APPROVED, SurgeryOrder.Status.SCHEDULED):
        messages.error(request, 'Order must be approved before scheduling.')
        return redirect('surgery_order_detail', order_id=order_id)

    existing  = getattr(order, 'schedule', None)
    rooms     = ORRoom.objects.filter(is_active=True)
    nurses    = _nurses()
    anesthesiologists = _anesthesiologists()

    if request.method == 'POST':
        p = request.POST
        room_id = p.get('or_room')
        if not room_id:
            messages.error(request, 'OR Room is required.')
        else:
            room = get_object_or_404(ORRoom, pk=room_id)
            if existing:
                existing.or_room             = room
                existing.scheduled_date       = p.get('scheduled_date')
                existing.scheduled_start_time = p.get('scheduled_start_time')
                existing.estimated_end_time   = p.get('estimated_end_time') or None
                existing.scrub_nurse_id       = p.get('scrub_nurse') or None
                existing.circulating_nurse_id = p.get('circulating_nurse') or None
                existing.anesthesiologist_id  = p.get('anesthesiologist') or None
                existing.notes                = p.get('notes', '').strip()
                existing.save()
                sched = existing
            else:
                sched = SurgerySchedule.objects.create(
                    surgery_order         = order,
                    or_room               = room,
                    scheduled_date        = p.get('scheduled_date'),
                    scheduled_start_time  = p.get('scheduled_start_time'),
                    estimated_end_time    = p.get('estimated_end_time') or None,
                    scrub_nurse_id        = p.get('scrub_nurse') or None,
                    circulating_nurse_id  = p.get('circulating_nurse') or None,
                    anesthesiologist_id   = p.get('anesthesiologist') or None,
                    notes                 = p.get('notes', '').strip(),
                    scheduled_by          = request.user,
                )
                order.status = SurgeryOrder.Status.SCHEDULED
                order.save()

            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                object_type='SurgerySchedule', object_id=sched.pk,
                object_repr=order.order_number,
                description=(
                    f'Surgery scheduled: {order.order_number} in {room.name} '
                    f'on {sched.scheduled_date} at {sched.scheduled_start_time}'
                ),
                request=request,
            )
            messages.success(request, 'Surgery scheduled successfully.')
            return redirect('surgery_order_detail', order_id=order_id)

    return render(request, 'surgery/schedule_form.html', {
        'order':             order,
        'schedule':          existing,
        'rooms':             rooms,
        'nurses':            nurses,
        'anesthesiologists': anesthesiologists,
    })


# ── Procedure Master List ─────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def procedure_master_list(request):
    qs = ProcedureMaster.objects.select_related('category', 'department')

    q           = request.GET.get('q', '').strip()
    category_f  = request.GET.get('category', '').strip()
    complexity_f = request.GET.get('complexity', '').strip()
    active_f    = request.GET.get('active', 'true')

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if category_f:
        qs = qs.filter(category_id=category_f)
    if complexity_f:
        qs = qs.filter(complexity=complexity_f)
    if active_f == 'true':
        qs = qs.filter(is_active=True)
    elif active_f == 'false':
        qs = qs.filter(is_active=False)

    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'surgery/procedure_list.html', {
        'page_obj':    page_obj,
        'categories':  ProcedureCategory.objects.filter(is_active=True),
        'complexities': ProcedureMaster.Complexity.choices,
        'q':           q,
        'category_f':  category_f,
        'complexity_f': complexity_f,
        'active_f':    active_f,
    })


# ── Procedure Master Create / Edit ────────────────────────────────────────────

@hms_permission_required('core.manage_procedure_master')
def procedure_master_create(request):
    categories  = ProcedureCategory.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True)

    if request.method == 'POST':
        p = request.POST
        try:
            proc = ProcedureMaster.objects.create(
                name                       = p['name'].strip(),
                code                       = p['code'].strip().upper(),
                category_id                = p.get('category') or None,
                department_id              = p.get('department') or None,
                description                = p.get('description', '').strip(),
                required_specialty         = p.get('required_specialty', '').strip(),
                estimated_duration_minutes = int(p.get('estimated_duration_minutes', 60) or 60),
                complexity                 = p.get('complexity', ProcedureMaster.Complexity.MODERATE),
                indications                = p.get('indications', '').strip(),
                contraindications          = p.get('contraindications', '').strip(),
                required_investigations    = p.get('required_investigations', '').strip(),
                required_preparation       = p.get('required_preparation', '').strip(),
                required_equipment         = p.get('required_equipment', '').strip(),
                required_medications       = p.get('required_medications', '').strip(),
                required_consumables       = p.get('required_consumables', '').strip(),
                or_type_required           = p.get('or_type_required', '').strip(),
                required_instruments       = p.get('required_instruments', '').strip(),
                required_implants          = p.get('required_implants', '').strip(),
                required_anesthesia_type   = p.get('required_anesthesia_type', ProcedureMaster.AnesthesiaType.GENERAL),
                procedure_price    = p.get('procedure_price', 0) or 0,
                surgeon_fee        = p.get('surgeon_fee', 0) or 0,
                anesthesia_fee     = p.get('anesthesia_fee', 0) or 0,
                facility_fee       = p.get('facility_fee', 0) or 0,
                consumable_charges = p.get('consumable_charges', 0) or 0,
                insurance_price    = p.get('insurance_price', 0) or 0,
                billing_category   = p.get('billing_category', '').strip(),
                is_active          = True,
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                object_type='ProcedureMaster', object_id=proc.pk, object_repr=proc.name,
                description=f'Procedure master created: {proc.code} — {proc.name}',
                request=request,
            )
            messages.success(request, f'Procedure "{proc.name}" created.')
            return redirect('procedure_master_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/procedure_form.html', {
        'categories':        categories,
        'departments':       departments,
        'complexities':      ProcedureMaster.Complexity.choices,
        'anesthesia_types':  ProcedureMaster.AnesthesiaType.choices,
        'action':            'Create',
    })


@hms_permission_required('core.manage_procedure_master')
def procedure_master_edit(request, proc_id):
    proc        = get_object_or_404(ProcedureMaster, pk=proc_id)
    categories  = ProcedureCategory.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True)

    if request.method == 'POST':
        p = request.POST
        try:
            proc.name                       = p['name'].strip()
            proc.code                       = p['code'].strip().upper()
            proc.category_id                = p.get('category') or None
            proc.department_id              = p.get('department') or None
            proc.description                = p.get('description', '').strip()
            proc.required_specialty         = p.get('required_specialty', '').strip()
            proc.estimated_duration_minutes = int(p.get('estimated_duration_minutes', 60) or 60)
            proc.complexity                 = p.get('complexity', proc.complexity)
            proc.indications                = p.get('indications', '').strip()
            proc.contraindications          = p.get('contraindications', '').strip()
            proc.required_investigations    = p.get('required_investigations', '').strip()
            proc.required_preparation       = p.get('required_preparation', '').strip()
            proc.required_equipment         = p.get('required_equipment', '').strip()
            proc.required_medications       = p.get('required_medications', '').strip()
            proc.required_consumables       = p.get('required_consumables', '').strip()
            proc.or_type_required           = p.get('or_type_required', '').strip()
            proc.required_instruments       = p.get('required_instruments', '').strip()
            proc.required_implants          = p.get('required_implants', '').strip()
            proc.required_anesthesia_type   = p.get('required_anesthesia_type', proc.required_anesthesia_type)
            proc.procedure_price    = p.get('procedure_price', proc.procedure_price) or proc.procedure_price
            proc.surgeon_fee        = p.get('surgeon_fee', proc.surgeon_fee) or proc.surgeon_fee
            proc.anesthesia_fee     = p.get('anesthesia_fee', proc.anesthesia_fee) or proc.anesthesia_fee
            proc.facility_fee       = p.get('facility_fee', proc.facility_fee) or proc.facility_fee
            proc.consumable_charges = p.get('consumable_charges', proc.consumable_charges) or proc.consumable_charges
            proc.insurance_price    = p.get('insurance_price', proc.insurance_price) or proc.insurance_price
            proc.billing_category   = p.get('billing_category', '').strip()
            proc.is_active          = bool(p.get('is_active'))
            proc.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                object_type='ProcedureMaster', object_id=proc.pk, object_repr=proc.name,
                description=f'Procedure master updated: {proc.code} — {proc.name}',
                request=request,
            )
            messages.success(request, f'Procedure "{proc.name}" updated.')
            return redirect('procedure_master_detail', proc_id=proc.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/procedure_form.html', {
        'proc':              proc,
        'categories':        categories,
        'departments':       departments,
        'complexities':      ProcedureMaster.Complexity.choices,
        'anesthesia_types':  ProcedureMaster.AnesthesiaType.choices,
        'action':            'Edit',
    })


@hms_permission_required('core.read_surgery')
def procedure_master_detail(request, proc_id):
    proc = get_object_or_404(
        ProcedureMaster.objects.select_related('category', 'department'),
        pk=proc_id,
    )
    recent_orders = SurgeryOrder.objects.filter(
        procedure_master=proc,
    ).select_related('patient', 'surgeon').order_by('-ordered_at')[:10]

    return render(request, 'surgery/procedure_detail.html', {
        'proc':          proc,
        'recent_orders': recent_orders,
    })


# ── OR Room Management ────────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_room_list(request):
    rooms = ORRoom.objects.all()
    return render(request, 'surgery/or_room_list.html', {'rooms': rooms})


def _or_room_facility_context():
    from .models import Building, Department, Floor
    return {
        'departments': Department.objects.filter(is_active=True).order_by('name'),
        'buildings':   Building.objects.filter(is_active=True).order_by('name'),
        'floors':      Floor.objects.filter(is_active=True).select_related('building').order_by('building__name', 'level_order'),
        'availability_choices': ORRoom.Availability.choices,
    }


@hms_permission_required('core.manage_or_schedule')
def or_room_create(request):
    if request.method == 'POST':
        p = request.POST
        try:
            room = ORRoom.objects.create(
                name      = p['name'].strip(),
                code      = p.get('code', '').strip(),
                room_type = p.get('room_type', ORRoom.RoomType.GENERAL),
                department_id = p.get('department') or None,
                building_id   = p.get('building') or None,
                floor_id      = p.get('floor') or None,
                location  = p.get('location', '').strip(),
                capacity  = int(p.get('capacity', 1) or 1),
                equipment = p.get('equipment', '').strip(),
                notes     = p.get('notes', '').strip(),
                availability_status = p.get('availability_status', ORRoom.Availability.AVAILABLE),
                daily_open_time  = p.get('daily_open_time') or None,
                daily_close_time = p.get('daily_close_time') or None,
                max_surgeries_per_day = p.get('max_surgeries_per_day') or None,
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='ORRoom', object_id=room.pk, object_repr=room.name,
                description=f'OR Room "{room.name}" created', request=request,
            )
            messages.success(request, f'OR Room "{room.name}" created.')
            return redirect('or_room_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/or_room_form.html', {
        'room_types': ORRoom.RoomType.choices,
        'action':     'Create',
        **_or_room_facility_context(),
    })


@hms_permission_required('core.manage_or_schedule')
def or_room_edit(request, room_id):
    room = get_object_or_404(ORRoom, pk=room_id)
    if request.method == 'POST':
        p = request.POST
        try:
            room.name      = p['name'].strip()
            room.code      = p.get('code', '').strip()
            room.room_type = p.get('room_type', room.room_type)
            room.department_id = p.get('department') or None
            room.building_id   = p.get('building') or None
            room.floor_id      = p.get('floor') or None
            room.location  = p.get('location', '').strip()
            room.capacity  = int(p.get('capacity', room.capacity) or room.capacity)
            room.equipment = p.get('equipment', '').strip()
            room.notes     = p.get('notes', '').strip()
            room.availability_status = p.get('availability_status', room.availability_status)
            room.daily_open_time  = p.get('daily_open_time') or None
            room.daily_close_time = p.get('daily_close_time') or None
            room.max_surgeries_per_day = p.get('max_surgeries_per_day') or None
            room.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='ORRoom', object_id=room.pk, object_repr=room.name,
                description=f'OR Room "{room.name}" updated', request=request,
            )
            messages.success(request, f'OR Room "{room.name}" updated.')
            return redirect('or_room_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/or_room_form.html', {
        'room':       room,
        'room_types': ORRoom.RoomType.choices,
        'action':     'Edit',
        **_or_room_facility_context(),
    })


# ── Anesthesia Record / Pre-Operative Anesthetic Assessment ───────────────────

ANESTHESIA_TEXT_FIELDS = [
    'present_illness_history', 'past_medical_history', 'previous_surgeries_history',
    'previous_anesthesia_history', 'previous_anesthesia_complications', 'known_allergies',
    'current_medications', 'chronic_diseases', 'smoking_status', 'alcohol_use',
    'alcohol_details', 'pregnancy_status',
    'general_appearance', 'cvs_exam', 'respiratory_exam', 'neuro_exam',
    'mallampati_class', 'mouth_opening', 'neck_mobility', 'dentition_notes',
    'difficult_airway_notes',
    'asa_classification', 'pre_assessment_notes',
    'lab_review_cbc', 'lab_review_blood_group', 'lab_review_coagulation',
    'lab_review_blood_sugar', 'lab_review_rft', 'lab_review_lft', 'lab_review_ecg',
    'lab_review_cxr', 'lab_review_other',
    'anesthesia_type_planned', 'anesthesia_technique', 'airway_management',
    'monitoring_plan', 'blood_products_required', 'special_equipment_needed',
    'risk_assessment', 'pre_medication', 'npo_fasting_status',
    'final_assessment', 'fitness_for_surgery', 'recommendations', 'assessment_comments',
    'induction_agent', 'maintenance_agent', 'monitoring_notes', 'intraop_complications',
    'post_anesthesia_notes', 'pacu_complications',
]
ANESTHESIA_INT_FIELDS = ['pe_bp_systolic', 'pe_bp_diastolic', 'pe_pulse', 'pe_respiratory_rate',
                         'smoking_pack_years', 'pacu_duration_minutes']
ANESTHESIA_DEC_FIELDS = ['pe_temperature', 'pe_spo2', 'pe_weight_kg', 'pe_height_cm',
                         'thyromental_distance_cm']
ANESTHESIA_DT_FIELDS  = ['induction_time', 'incision_time', 'closure_time', 'extubation_time']
ANESTHESIA_BOOL_FIELDS = ['difficult_airway_anticipated']
ANESTHESIA_ALL_FIELDS = (
    ANESTHESIA_TEXT_FIELDS + ANESTHESIA_INT_FIELDS + ANESTHESIA_DEC_FIELDS
    + ANESTHESIA_DT_FIELDS + ANESTHESIA_BOOL_FIELDS
)


def _apply_anesthesia_form(rec, post):
    for f in ANESTHESIA_TEXT_FIELDS:
        setattr(rec, f, _pstr(post, f))
    for f in ANESTHESIA_INT_FIELDS:
        setattr(rec, f, _pint(post, f))
    for f in ANESTHESIA_DEC_FIELDS:
        setattr(rec, f, _pdec(post, f))
    for f in ANESTHESIA_DT_FIELDS:
        setattr(rec, f, _pdatetime(post, f))
    for f in ANESTHESIA_BOOL_FIELDS:
        setattr(rec, f, _pbool(post, f))


@hms_permission_required('core.write_surgery_anesthesia')
def surgery_anesthesia_create(request, order_id):
    order    = get_object_or_404(SurgeryOrder, pk=order_id)
    existing = order.current_anesthesia_record

    if request.method == 'POST':
        p = request.POST
        try:
            with transaction.atomic():
                if existing and existing.is_finalized:
                    reason = p.get('revision_reason', '').strip()
                    if not reason:
                        messages.error(request, 'A reason is required to revise a finalized assessment.')
                        return redirect('surgery_anesthesia_create', order_id=order_id)
                    rec = _revise_periop_document(
                        SurgeryAnesthesiaRecord, existing, request.user, reason, ANESTHESIA_ALL_FIELDS,
                    )
                    is_new = True
                elif existing:
                    rec = existing
                    is_new = False
                else:
                    rec = SurgeryAnesthesiaRecord(surgery_order=order, created_by=request.user)
                    is_new = False

                old_snapshot = copy.copy(rec) if rec.pk else None
                _apply_anesthesia_form(rec, p)
                rec.updated_by = request.user
                rec.save()

            changes = build_changes(old_snapshot, rec, ANESTHESIA_ALL_FIELDS) if old_snapshot else None
            log_action(
                request.user, AuditLog.Action.CREATE if is_new or not existing else AuditLog.Action.UPDATE,
                AuditLog.Module.SURGERY,
                object_type='SurgeryAnesthesiaRecord', object_id=rec.pk,
                object_repr=f'{order.order_number} v{rec.version}',
                description=f'Pre-operative anesthetic assessment {"revised (new version)" if is_new and existing else "saved"} for {order.order_number}',
                changes=changes,
                request=request,
            )
            messages.success(request, 'Anesthesia assessment saved as draft.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/anesthesia_form.html', {
        'order':  order,
        'record': existing,
        'asa_choices': SurgeryAnesthesiaRecord.ASA.choices,
        'smoking_choices': SurgeryAnesthesiaRecord.Smoking.choices,
        'alcohol_choices': SurgeryAnesthesiaRecord.Alcohol.choices,
        'pregnancy_choices': SurgeryAnesthesiaRecord.Pregnancy.choices,
        'mallampati_choices': SurgeryAnesthesiaRecord.Mallampati.choices,
        'neck_mobility_choices': SurgeryAnesthesiaRecord.NeckMobility.choices,
        'fitness_choices': SurgeryAnesthesiaRecord.Fitness.choices,
        'anesthesia_type_choices': SurgeryOrder.AnesthesiaType.choices,
        'header': _periop_header_context(order),
    })


@require_POST
@hms_permission_required('core.write_surgery_anesthesia')
def surgery_anesthesia_finalize(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    rec = order.current_anesthesia_record
    if not rec or rec.is_finalized:
        messages.error(request, 'No draft anesthesia assessment to finalize.')
        return redirect('surgery_order_detail', order_id=order_id)
    if not request.POST.get('certify'):
        messages.error(request, 'You must certify the record is accurate and complete to finalize.')
        return redirect('surgery_anesthesia_create', order_id=order_id)
    _finalize_periop_document(rec, request.user)
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.SURGERY,
        object_type='SurgeryAnesthesiaRecord', object_id=rec.pk,
        object_repr=f'{order.order_number} v{rec.version}',
        description=f'Pre-operative anesthetic assessment finalized & signed for {order.order_number}',
        request=request,
    )
    messages.success(request, 'Anesthesia assessment finalized and signed.')
    return redirect('surgery_order_detail', order_id=order_id)


@hms_permission_required('core.read_periop_document_history')
def surgery_anesthesia_history(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    versions = order.anesthesia_records.select_related('created_by', 'finalized_by').order_by('-version')
    return render(request, 'surgery/anesthesia_history.html', {'order': order, 'versions': versions})


@hms_permission_required('core.read_surgery')
def surgery_anesthesia_print(request, order_id, version=None):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    if version:
        doc = get_object_or_404(order.anesthesia_records, version=version)
    else:
        doc = order.current_anesthesia_record
        if not doc:
            messages.error(request, 'No anesthesia assessment exists yet for this order.')
            return redirect('surgery_order_detail', order_id=order_id)
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.SURGERY,
        object_type='SurgeryAnesthesiaRecord', object_id=doc.pk,
        object_repr=f'{order.order_number} v{doc.version}',
        description=f'Printed anesthesia assessment v{doc.version} for {order.order_number}',
        request=request,
    )
    return render(request, 'surgery/anesthesia_print.html', {
        'order': order, 'doc': doc, 'header': _periop_header_context(order), 'now': timezone.now(),
    })


# ── OR Operative Note ──────────────────────────────────────────────────────────

OPNOTE_TEXT_FIELDS = [
    'procedure_performed', 'pre_op_diagnosis', 'post_op_diagnosis', 'anesthesia_type',
    'incision_type', 'surgical_technique', 'findings', 'procedure_steps', 'complications',
    'specimens_collected', 'implants_used', 'drains_placed', 'counts_correct',
    'wound_classification', 'wound_closure', 'dressings_applied',
    'blood_transfusion_details', 'intraop_events', 'unexpected_findings', 'recommendations',
    'post_op_instructions',
]
OPNOTE_INT_FIELDS  = ['blood_loss_ml', 'urine_output_ml']
OPNOTE_DT_FIELDS   = ['time_patient_entered_or', 'time_surgery_start', 'time_surgery_end', 'time_patient_left_or']
OPNOTE_BOOL_FIELDS = ['blood_transfusion_given']
OPNOTE_ALL_FIELDS  = OPNOTE_TEXT_FIELDS + OPNOTE_INT_FIELDS + OPNOTE_DT_FIELDS + OPNOTE_BOOL_FIELDS


def _apply_opnote_form(note, post):
    for f in OPNOTE_TEXT_FIELDS:
        setattr(note, f, _pstr(post, f))
    if not note.procedure_performed:
        note.procedure_performed = note.surgery_order.planned_procedure
    for f in OPNOTE_INT_FIELDS:
        setattr(note, f, _pint(post, f))
    for f in OPNOTE_DT_FIELDS:
        setattr(note, f, _pdatetime(post, f))
    for f in OPNOTE_BOOL_FIELDS:
        setattr(note, f, _pbool(post, f))


@hms_permission_required('core.write_operative_note')
def operative_note_create(request, order_id):
    order    = get_object_or_404(SurgeryOrder, pk=order_id)
    existing = order.current_operative_note

    if request.method == 'POST':
        p = request.POST
        assistant_ids = [pk for pk in p.getlist('assistant_surgeons') if pk]
        try:
            with transaction.atomic():
                if existing and existing.is_finalized:
                    reason = p.get('revision_reason', '').strip()
                    if not reason:
                        messages.error(request, 'A reason is required to revise a finalized operative note.')
                        return redirect('operative_note_create', order_id=order_id)
                    note = _revise_periop_document(
                        OperativeNote, existing, request.user, reason, OPNOTE_ALL_FIELDS,
                        m2m_field_names=['assistant_surgeons'],
                    )
                    is_new = True
                elif existing:
                    note = existing
                    is_new = False
                else:
                    note = OperativeNote(surgery_order=order, created_by=request.user)
                    is_new = False

                old_snapshot = copy.copy(note) if note.pk else None
                _apply_opnote_form(note, p)
                note.updated_by = request.user
                note.save()
                note.assistant_surgeons.set(assistant_ids)

                submitting = p.get('action') == 'submit'
                if submitting:
                    if not p.get('certify'):
                        raise ValueError('You must check the certification box before submitting.')
                    _finalize_periop_document(note, request.user)

            changes = build_changes(old_snapshot, note, OPNOTE_ALL_FIELDS) if old_snapshot else None
            is_submit = p.get('action') == 'submit'
            log_action(
                request.user, AuditLog.Action.APPROVE if is_submit else (AuditLog.Action.CREATE if is_new or not existing else AuditLog.Action.UPDATE),
                AuditLog.Module.SURGERY,
                object_type='OperativeNote', object_id=note.pk,
                object_repr=f'{order.order_number} v{note.version}',
                description=f'Operative note {"submitted & signed" if is_submit else ("revised (new version)" if is_new and existing else "saved as draft")} for {order.order_number}',
                changes=changes,
                request=request,
            )
            if is_submit:
                messages.success(request, 'Operative note submitted and signed. Nurses can now view it.')
            else:
                messages.success(request, 'Operative note saved as draft.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/operative_note_form.html', {
        'order':  order,
        'note':   existing,
        'counts_correct_choices': OperativeNote.CountsCorrect.choices,
        'wound_class_choices': OperativeNote.WoundClass.choices,
        'anesthesia_type_choices': SurgeryOrder.AnesthesiaType.choices,
        'surgeons': _surgeons(),
        'header': _periop_header_context(order),
    })


@require_POST
@hms_permission_required('core.write_operative_note')
def operative_note_finalize(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    note = order.current_operative_note
    if not note or note.is_finalized:
        messages.error(request, 'No draft operative note to finalize.')
        return redirect('surgery_order_detail', order_id=order_id)
    if not request.POST.get('certify'):
        messages.error(request, 'You must certify the record is accurate and complete to finalize.')
        return redirect('operative_note_create', order_id=order_id)
    _finalize_periop_document(note, request.user)
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.SURGERY,
        object_type='OperativeNote', object_id=note.pk,
        object_repr=f'{order.order_number} v{note.version}',
        description=f'Operative note finalized & signed for {order.order_number}',
        request=request,
    )
    messages.success(request, 'Operative note finalized and signed.')
    return redirect('surgery_order_detail', order_id=order_id)


@hms_permission_required('core.read_periop_document_history')
def operative_note_history(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    versions = order.operative_notes.select_related('created_by', 'finalized_by').order_by('-version')
    return render(request, 'surgery/operative_note_history.html', {'order': order, 'versions': versions})


@hms_permission_required('core.read_surgery')
def operative_note_print(request, order_id, version=None):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    if version:
        doc = get_object_or_404(order.operative_notes, version=version)
    else:
        doc = order.current_operative_note
        if not doc:
            messages.error(request, 'No operative note exists yet for this order.')
            return redirect('surgery_order_detail', order_id=order_id)
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.SURGERY,
        object_type='OperativeNote', object_id=doc.pk,
        object_repr=f'{order.order_number} v{doc.version}',
        description=f'Printed operative note v{doc.version} for {order.order_number}',
        request=request,
    )
    return render(request, 'surgery/operative_note_print.html', {
        'order': order, 'doc': doc, 'header': _periop_header_context(order), 'now': timezone.now(),
    })


# ── Post-Operative Note ────────────────────────────────────────────────────────

POSTOP_TEXT_FIELDS = [
    'consciousness_level', 'airway_status', 'o2_requirement', 'neuro_status',
    'wound_condition', 'drain_status', 'catheters_present', 'bleeding_assessment',
    'post_op_complications', 'immediate_post_op_diagnosis',
    'diet_orders', 'iv_fluids_orders', 'medication_orders', 'antibiotic_orders',
    'pain_management_plan', 'dvt_prophylaxis', 'physiotherapy_orders',
    'nursing_instructions', 'activity_level', 'follow_up_instructions',
    'lab_orders', 'imaging_orders', 'disposition', 'disposition_notes',
    'dressing_change_plan', 'drain_removal_plan', 'suture_removal_plan',
    'additional_procedures_planned', 'outpatient_followup_instructions',
]
POSTOP_INT_FIELDS  = ['pain_score', 'po_bp_systolic', 'po_bp_diastolic', 'po_pulse', 'po_respiratory_rate']
POSTOP_DEC_FIELDS  = ['po_temperature', 'po_spo2']
POSTOP_DATE_FIELDS = ['review_date']
POSTOP_ALL_FIELDS  = POSTOP_TEXT_FIELDS + POSTOP_INT_FIELDS + POSTOP_DEC_FIELDS + POSTOP_DATE_FIELDS


def _apply_postop_form(note, post):
    for f in POSTOP_TEXT_FIELDS:
        setattr(note, f, _pstr(post, f))
    for f in POSTOP_INT_FIELDS:
        setattr(note, f, _pint(post, f))
    for f in POSTOP_DEC_FIELDS:
        setattr(note, f, _pdec(post, f))
    for f in POSTOP_DATE_FIELDS:
        setattr(note, f, _pdate(post, f))


@hms_permission_required('core.write_postop_note')
def postop_note_create(request, order_id):
    order    = get_object_or_404(SurgeryOrder, pk=order_id)
    existing = order.current_postop_note

    if request.method == 'POST':
        p = request.POST
        try:
            with transaction.atomic():
                if existing and existing.is_finalized:
                    reason = p.get('revision_reason', '').strip()
                    if not reason:
                        messages.error(request, 'A reason is required to revise a finalized post-operative note.')
                        return redirect('postop_note_create', order_id=order_id)
                    note = _revise_periop_document(
                        PostOperativeNote, existing, request.user, reason, POSTOP_ALL_FIELDS,
                    )
                    is_new = True
                elif existing:
                    note = existing
                    is_new = False
                else:
                    note = PostOperativeNote(surgery_order=order, created_by=request.user)
                    is_new = False

                old_snapshot = copy.copy(note) if note.pk else None
                _apply_postop_form(note, p)
                note.updated_by = request.user
                note.save()

                submitting = p.get('action') == 'submit'
                if submitting:
                    if not p.get('certify'):
                        raise ValueError('You must check the certification box before submitting.')
                    _finalize_periop_document(note, request.user)

            changes = build_changes(old_snapshot, note, POSTOP_ALL_FIELDS) if old_snapshot else None
            is_submit = p.get('action') == 'submit'
            log_action(
                request.user, AuditLog.Action.APPROVE if is_submit else (AuditLog.Action.CREATE if is_new or not existing else AuditLog.Action.UPDATE),
                AuditLog.Module.SURGERY,
                object_type='PostOperativeNote', object_id=note.pk,
                object_repr=f'{order.order_number} v{note.version}',
                description=f'Post-operative note {"submitted & signed" if is_submit else ("revised (new version)" if is_new and existing else "saved as draft")} for {order.order_number}',
                changes=changes,
                request=request,
            )
            if is_submit:
                messages.success(request, 'Post-operative note submitted and signed. Nurses can now view it.')
            else:
                messages.success(request, 'Post-operative note saved as draft.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/postop_note_form.html', {
        'order':  order,
        'note':   existing,
        'consciousness_choices': PostOperativeNote.Consciousness.choices,
        'disposition_choices': PostOperativeNote.Disposition.choices,
        'header': _periop_header_context(order),
    })


@require_POST
@hms_permission_required('core.write_postop_note')
def postop_note_finalize(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    note = order.current_postop_note
    if not note or note.is_finalized:
        messages.error(request, 'No draft post-operative note to finalize.')
        return redirect('surgery_order_detail', order_id=order_id)
    if not request.POST.get('certify'):
        messages.error(request, 'You must certify the record is accurate and complete to finalize.')
        return redirect('postop_note_create', order_id=order_id)
    _finalize_periop_document(note, request.user)
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.SURGERY,
        object_type='PostOperativeNote', object_id=note.pk,
        object_repr=f'{order.order_number} v{note.version}',
        description=f'Post-operative note finalized & signed for {order.order_number}',
        request=request,
    )
    messages.success(request, 'Post-operative note finalized and signed.')
    return redirect('surgery_order_detail', order_id=order_id)


@hms_permission_required('core.read_periop_document_history')
def postop_note_history(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    versions = order.postop_notes.select_related('created_by', 'finalized_by').order_by('-version')
    return render(request, 'surgery/postop_note_history.html', {'order': order, 'versions': versions})


@hms_permission_required('core.read_postop_note')
def postop_note_print(request, order_id, version=None):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    if version:
        doc = get_object_or_404(order.postop_notes, version=version)
    else:
        doc = order.current_postop_note
        if not doc:
            messages.error(request, 'No post-operative note exists yet for this order.')
            return redirect('surgery_order_detail', order_id=order_id)
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.SURGERY,
        object_type='PostOperativeNote', object_id=doc.pk,
        object_repr=f'{order.order_number} v{doc.version}',
        description=f'Printed post-operative note v{doc.version} for {order.order_number}',
        request=request,
    )
    return render(request, 'surgery/postop_note_print.html', {
        'order': order, 'doc': doc, 'header': _periop_header_context(order), 'now': timezone.now(),
    })


# ── Perioperative Nursing Addenda ──────────────────────────────────────────────

@require_POST
@hms_permission_required('core.add_periop_nursing_note')
def periop_nursing_addendum_add(request, order_id, document_type):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    valid_types = {c[0] for c in PeriopNursingAddendum.DocumentType.choices}
    if document_type not in valid_types:
        messages.error(request, 'Invalid document type.')
        return redirect('surgery_order_detail', order_id=order_id)
    note = request.POST.get('note', '').strip()
    if not note:
        messages.error(request, 'Nursing note cannot be empty.')
        return redirect('surgery_order_detail', order_id=order_id)
    addendum = PeriopNursingAddendum.objects.create(
        surgery_order=order, document_type=document_type, note=note, created_by=request.user,
    )
    log_action(
        request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
        object_type='PeriopNursingAddendum', object_id=addendum.pk,
        object_repr=f'{order.order_number} ({document_type})',
        description=f'Nursing addendum added to {document_type} for {order.order_number}',
        request=request,
    )
    messages.success(request, 'Nursing note added.')
    return redirect('surgery_order_detail', order_id=order_id)


# ── Surgery Consumables ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_surgery_consumables')
@require_POST
def surgery_consumable_add(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    p = request.POST
    try:
        qty = Decimal(str(p.get('quantity', 1) or 1))
        unit_cost = p.get('unit_cost', 0) or 0
        inv_item_id = p.get('inventory_item') or None
        inv_item = None

        if inv_item_id:
            inv_item = get_object_or_404(InventoryItem, pk=inv_item_id, item_type=InventoryItem.ItemType.SURGICAL)
            if inv_item.quantity_in_stock < qty:
                messages.error(
                    request,
                    f'Insufficient stock for "{inv_item.name}". Available: {inv_item.quantity_in_stock} {inv_item.unit}.'
                )
                return redirect('surgery_order_detail', order_id=order_id)
            if not unit_cost:
                unit_cost = inv_item.unit_cost

        item_name = p.get('item_name', '').strip() or (inv_item.name if inv_item else '')
        if not item_name:
            messages.error(request, 'Provide an item name or select a catalogue item.')
            return redirect('surgery_order_detail', order_id=order_id)

        with transaction.atomic():
            SurgeryConsumable.objects.create(
                surgery_order  = order,
                item_type      = p.get('item_type', SurgeryConsumable.ItemType.SUPPLY),
                inventory_item = inv_item,
                item_name      = item_name,
                quantity       = qty,
                unit           = p.get('unit', 'unit').strip() or 'unit',
                batch_number   = p.get('batch_number', '').strip(),
                expiry_date    = p.get('expiry_date') or None,
                unit_cost      = unit_cost,
                recorded_by    = request.user,
            )

            if inv_item:
                inv_item.quantity_in_stock -= qty
                inv_item.save(update_fields=['quantity_in_stock'])
                or_dept = Department.objects.filter(name__icontains='Operating').first() or Department.objects.filter(name__icontains='Surgery').first()
                InventoryTransaction.objects.create(
                    inventory_item=inv_item,
                    transaction_type=InventoryTransaction.TxType.ISSUE,
                    quantity_out=qty,
                    balance_after=inv_item.quantity_in_stock,
                    unit_cost=inv_item.unit_cost,
                    reference_number=order.order_number,
                    department=or_dept,
                    notes=f'Consumed for surgery {order.order_number} — {order.patient.full_name}.',
                    performed_by=request.user,
                )

        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryConsumable', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Consumable added: {item_name} × {qty} for {order.order_number}',
            request=request,
        )
        messages.success(request, f'Consumable "{item_name}" recorded.')
    except Exception as exc:
        messages.error(request, f'Error: {exc}')
    return redirect('surgery_order_detail', order_id=order_id)


# ── Surgery Billing Generate ──────────────────────────────────────────────────

@hms_permission_required('core.generate_surgery_billing')
@require_POST
def surgery_billing_generate(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)

    if order.invoice:
        messages.warning(request, 'Billing already generated for this order.')
        return redirect('surgery_order_detail', order_id=order_id)

    if order.status not in (
        SurgeryOrder.Status.COMPLETED, SurgeryOrder.Status.POST_OP,
        SurgeryOrder.Status.SCHEDULED, SurgeryOrder.Status.APPROVED,
        SurgeryOrder.Status.IN_OR,
    ):
        messages.error(request, 'Billing can only be generated for approved/scheduled/in-OR/completed orders.')
        return redirect('surgery_order_detail', order_id=order_id)

    from decimal import Decimal
    pm = order.procedure_master

    with transaction.atomic():
        # Find or create an invoice for this visit/patient
        invoice = Invoice.objects.create(
            patient      = order.patient,
            visit        = order.visit,
            created_by   = request.user,
            status       = 'Draft',
            total_amount = Decimal('0'),
            due_date     = timezone.localdate(),
        )

        # Core procedure charge (procedure/surgeon/anesthesia/facility fees)
        # is billed as ONE combined line item so the whole "core surgery" is
        # paid/gated as a single unit, linked back to the order itself.
        core_fee = Decimal('0')
        if pm:
            for fee in (pm.procedure_price, pm.surgeon_fee, pm.anesthesia_fee, pm.facility_fee, pm.consumable_charges):
                if fee:
                    core_fee += Decimal(str(fee))

        total = Decimal('0')
        if core_fee > 0:
            core_item = InvoiceItem.objects.create(
                invoice      = invoice,
                description  = f'Surgery Charges — {order.planned_procedure}',
                service_type = 'SURGERY',
                quantity     = Decimal('1'),
                unit_price   = core_fee,
                total        = core_fee,
            )
            order.invoice_item = core_item
            total += core_fee

        # Each tracked consumable is billed — and therefore payment-gated —
        # as its own separate line item.
        for con in order.consumables.all():
            if con.total_cost:
                price = Decimal(str(con.total_cost))
                con_item = InvoiceItem.objects.create(
                    invoice      = invoice,
                    description  = f'Consumable: {con.item_name}',
                    service_type = 'SURGERY',
                    quantity     = Decimal('1'),
                    unit_price   = price,
                    total        = price,
                )
                con.invoice_item = con_item
                con.save(update_fields=['invoice_item'])
                total += price

        invoice.total_amount = total
        invoice.save()

        order.invoice = invoice
        order.save()

    log_action(
        request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
        object_type='Invoice', object_id=invoice.pk,
        object_repr=f'Surgery billing — {order.order_number}',
        description=(
            f'Surgery billing generated: {order.order_number} '
            f'({order.patient.full_name}) — ETB {total:,.2f}'
        ),
        extra_data={'order_number': order.order_number, 'total': str(total)},
        request=request,
    )
    messages.success(request, f'Invoice generated — ETB {total:,.2f}. Proceed to billing for payment.')
    return redirect('surgery_order_detail', order_id=order_id)


# ── Surgery Reports ───────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery_reports')
def surgery_reports(request):
    today      = timezone.localdate()
    this_month = today.replace(day=1)

    orders = SurgeryOrder.objects.all()
    stats = {
        'total':         orders.count(),
        'completed':     orders.filter(status='completed').count(),
        'cancelled':     orders.filter(status='cancelled').count(),
        'in_or':         orders.filter(status='in_or').count(),
        'month_total':   orders.filter(ordered_at__date__gte=this_month).count(),
        'month_completed': orders.filter(status='completed', completed_at__date__gte=this_month).count(),
    }

    # Priority breakdown
    priority_stats = {
        'emergency': orders.filter(priority='emergency').count(),
        'urgent':    orders.filter(priority='urgent').count(),
        'elective':  orders.filter(priority='elective').count(),
    }

    return render(request, 'surgery/reports/index.html', {
        'stats':          stats,
        'priority_stats': priority_stats,
        'today':          today,
    })


@hms_permission_required('core.read_surgery_reports')
def report_surgery_schedule(request):
    today = timezone.localdate()
    days  = int(request.GET.get('days', 14))
    end   = today + timedelta(days=days)

    schedules = (
        SurgerySchedule.objects
        .filter(scheduled_date__gte=today, scheduled_date__lte=end)
        .select_related(
            'surgery_order__patient', 'surgery_order__surgeon',
            'surgery_order__procedure_master', 'or_room',
            'anesthesiologist',
        )
        .order_by('scheduled_date', 'scheduled_start_time')
    )

    return render(request, 'surgery/reports/schedule.html', {
        'schedules': schedules,
        'today':     today,
        'end':       end,
        'days':      days,
    })


@hms_permission_required('core.read_surgery_reports')
def report_completed_surgeries(request):
    qs = SurgeryOrder.objects.filter(status='completed').select_related(
        'patient', 'surgeon', 'procedure_master', 'department',
    )
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')
    surgeon_f = request.GET.get('surgeon', '')

    if date_from:
        qs = qs.filter(completed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(completed_at__date__lte=date_to)
    if surgeon_f:
        qs = qs.filter(surgeon_id=surgeon_f)

    qs = qs.order_by('-completed_at')
    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'surgery/reports/completed.html', {
        'page_obj':  page_obj,
        'surgeons':  _surgeons(),
        'date_from': date_from,
        'date_to':   date_to,
        'surgeon_f': surgeon_f,
    })


@hms_permission_required('core.read_surgery_reports')
def report_cancelled_surgeries(request):
    qs = SurgeryOrder.objects.filter(status='cancelled').select_related(
        'patient', 'surgeon', 'cancelled_by',
    )
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')
    if date_from:
        qs = qs.filter(cancelled_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(cancelled_at__date__lte=date_to)
    qs = qs.order_by('-cancelled_at')
    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'surgery/reports/cancelled.html', {
        'page_obj':  page_obj,
        'date_from': date_from,
        'date_to':   date_to,
    })


@hms_permission_required('core.read_surgery_reports')
def report_surgeon_performance(request):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    date_from   = request.GET.get('date_from', '')
    date_to     = request.GET.get('date_to', '')
    surgeon_id  = request.GET.get('surgeon', '')

    surgeons = User.objects.filter(
        surgery_orders_as_surgeon__isnull=False
    ).distinct().order_by('first_name', 'last_name')

    qs = SurgeryOrder.objects.all()
    if date_from:
        qs = qs.filter(ordered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(ordered_at__date__lte=date_to)
    if surgeon_id:
        qs = qs.filter(surgeon_id=surgeon_id)

    performance_data = (
        qs
        .values('surgeon__id', 'surgeon__first_name', 'surgeon__last_name')
        .annotate(
            total       = Count('id'),
            completed   = Count('id', filter=Q(status='completed')),
            cancelled   = Count('id', filter=Q(status='cancelled')),
            in_progress = Count('id', filter=Q(status__in=['in_or', 'post_op', 'recovery', 'patient_prepared'])),
            pending     = Count('id', filter=Q(status__in=['ordered', 'awaiting_decision', 'booking_deposit', 'awaiting_admission', 'scheduled'])),
            emergency   = Count('id', filter=Q(priority='emergency')),
        )
        .order_by('-total')
    )

    return render(request, 'surgery/reports/surgeon_performance.html', {
        'performance_data': performance_data,
        'surgeons':         surgeons,
        'selected_surgeon': surgeon_id,
        'date_from':        date_from,
        'date_to':          date_to,
    })


@hms_permission_required('core.read_surgery_reports')
def report_or_utilization(request):
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')

    qs = SurgerySchedule.objects.select_related('or_room', 'surgery_order__surgeon')
    if date_from:
        qs = qs.filter(scheduled_date__gte=date_from)
    if date_to:
        qs = qs.filter(scheduled_date__lte=date_to)

    room_stats = (
        qs
        .values('or_room__id', 'or_room__name', 'or_room__room_type')
        .annotate(
            total_scheduled = Count('id'),
            completed       = Count('id', filter=Q(surgery_order__status='completed')),
            cancelled       = Count('id', filter=Q(surgery_order__status='cancelled')),
        )
        .order_by('-total_scheduled')
    )

    # Reshape to a list of dicts the template can iterate
    utilization_data = [
        {
            'name': row['or_room__name'],
            'room_type': row['or_room__room_type'],
            'total_scheduled': row['total_scheduled'],
            'completed': row['completed'],
            'cancelled': row['cancelled'],
        }
        for row in room_stats
    ]

    return render(request, 'surgery/reports/or_utilization.html', {
        'utilization_data': utilization_data,
        'date_from':  date_from,
        'date_to':    date_to,
    })


@hms_permission_required('core.read_surgery_reports')
def report_or_inventory(request):
    """OR Inventory Report — stock on hand + recent consumption for surgical supplies."""
    items = InventoryItem.objects.filter(
        item_type=InventoryItem.ItemType.SURGICAL,
    ).select_related('category').order_by('name')

    status_f = request.GET.get('status', '')
    if status_f == 'low':
        items = [i for i in items if i.is_low_stock and not i.is_out_of_stock]
    elif status_f == 'out':
        items = [i for i in items if i.is_out_of_stock]

    total_value = sum(i.inventory_value for i in items)

    recent_tx = InventoryTransaction.objects.filter(
        inventory_item__item_type=InventoryItem.ItemType.SURGICAL,
    ).select_related('inventory_item', 'performed_by').order_by('-transaction_date')[:50]

    headers = ['Item', 'Category', 'Unit', 'In Stock', 'Reorder Level', 'Unit Cost', 'Value', 'Status']
    rows = [
        [item.name, str(item.category or ''), item.unit, item.quantity_in_stock,
         item.reorder_level, item.unit_cost, item.inventory_value, item.stock_status]
        for item in items
    ]

    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="or_inventory.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response

    if request.GET.get('export') == 'excel':
        return export_excel('or_inventory.xlsx', headers, rows, title='OR Inventory')

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'surgery/reports/or_inventory.html', {
        'items': items,
        'status_f': status_f,
        'total_value': total_value,
        'recent_tx': recent_tx,
        'qp': qp,
    })


# ── API Endpoints ─────────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def api_procedure_info(request, proc_id):
    from django.http import JsonResponse
    try:
        proc = ProcedureMaster.objects.get(pk=proc_id, is_active=True)
        return JsonResponse({
            'name': proc.name,
            'code': proc.code,
            'required_preparation': proc.required_preparation or '',
            'required_investigations': proc.required_investigations or '',
            'estimated_duration_minutes': proc.estimated_duration_minutes,
            'required_anesthesia_type': proc.required_anesthesia_type,
            'complexity': proc.complexity,
            'total_fee': str(proc.total_fee),
        })
    except ProcedureMaster.DoesNotExist:
        from django.http import JsonResponse
        return JsonResponse({'error': 'not found'}, status=404)


# ══════════════════════════════════════════════════════════════════════════════
# SURGERY BOOKING WORKFLOW — new views (Steps 2-6)
# ══════════════════════════════════════════════════════════════════════════════

@hms_permission_required('core.counsel_surgery_patient')
def surgery_counsel(request, order_id):
    """Step 2 — Reception counselling: view order details, procedure cost,
    record counselling notes, and capture the patient's decision."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'surgeon', 'procedure_master', 'department'),
        pk=order_id,
    )

    if request.method == 'POST':
        notes = request.POST.get('counseling_notes', '').strip()
        now   = timezone.now()
        order.counseling_notes  = notes
        order.counseling_done_by = request.user
        order.counseling_done_at = now
        if order.status == SurgeryOrder.Status.AWAITING_DECISION:
            pass  # stay in awaiting_decision until patient explicitly decides
        order.save(update_fields=['counseling_notes', 'counseling_done_by', 'counseling_done_at'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Counselling notes recorded for {order.order_number} ({order.patient.full_name})',
            request=request,
        )
        messages.success(request, 'Counselling notes saved.')
        return redirect('surgery_order_detail', order_id=order_id)

    return render(request, 'surgery/counsel_form.html', {'order': order})


@hms_permission_required('core.counsel_surgery_patient')
@require_POST
def surgery_patient_decision(request, order_id):
    """Step 3 — Record patient's decision (agreed / declined)."""
    order   = get_object_or_404(SurgeryOrder, pk=order_id)
    decision = request.POST.get('decision', '').strip()
    now      = timezone.now()

    if decision == 'agreed' and order.status in (
        SurgeryOrder.Status.AWAITING_DECISION, SurgeryOrder.Status.ORDERED,
        SurgeryOrder.Status.APPROVED,
    ):
        deposit_amount = request.POST.get('booking_deposit_amount', '0').strip()
        try:
            deposit_amount = Decimal(deposit_amount)
        except Exception:
            deposit_amount = Decimal('0')

        with transaction.atomic():
            order.patient_decision       = SurgeryOrder.PatientDecision.AGREED
            order.patient_decision_at    = now
            order.patient_decision_by    = request.user
            order.booking_deposit_amount = deposit_amount
            order.status                 = SurgeryOrder.Status.BOOKING_DEPOSIT
            order.save()

            if deposit_amount > 0:
                invoice = Invoice.objects.create(
                    patient      = order.patient,
                    visit        = order.visit,
                    created_by   = request.user,
                    status       = Invoice.Status.ISSUED,
                    total_amount = deposit_amount,
                    notes        = f'Surgery booking deposit — {order.planned_procedure} ({order.order_number})',
                )
                inv_item = InvoiceItem.objects.create(
                    invoice      = invoice,
                    service_type = InvoiceItem.ServiceType.SURGERY_BOOKING,
                    description  = f'Booking Deposit: {order.planned_procedure}',
                    quantity     = 1,
                    unit_price   = deposit_amount,
                )
                order.booking_deposit_invoice      = invoice
                order.booking_deposit_invoice_item = inv_item
                order.save(update_fields=['booking_deposit_invoice', 'booking_deposit_invoice_item'])

        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Patient agreed to surgery: {order.order_number} — booking deposit ETB {deposit_amount:,.2f} required',
            request=request,
        )
        messages.success(request, f'Patient agreed. Booking deposit invoice of ETB {deposit_amount:,.2f} created and sent to billing.')

    elif decision == 'declined' and order.status in (
        SurgeryOrder.Status.AWAITING_DECISION, SurgeryOrder.Status.ORDERED,
        SurgeryOrder.Status.APPROVED,
    ):
        reason = request.POST.get('decline_reason', '').strip()
        order.patient_decision        = SurgeryOrder.PatientDecision.DECLINED
        order.patient_decision_at     = now
        order.patient_decision_by     = request.user
        order.patient_decline_reason  = reason
        order.status                  = SurgeryOrder.Status.CANCELLED
        order.cancelled_by            = request.user
        order.cancelled_at            = now
        order.cancellation_reason     = f'Patient declined: {reason}' if reason else 'Patient declined surgery'
        order.save()
        log_action(
            request.user, AuditLog.Action.CANCEL, AuditLog.Module.SURGERY,
            object_type='SurgeryOrder', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Patient declined surgery: {order.order_number}. Reason: {reason or "—"}',
            severity=AuditLog.Severity.WARNING,
            request=request,
        )
        messages.warning(request, 'Surgery cancelled — patient declined.')

    else:
        messages.error(request, 'Invalid decision or order is not in the correct status.')

    return redirect('surgery_order_detail', order_id=order_id)


@hms_permission_required('core.process_surgery_booking_deposit')
def surgery_booking_deposit(request, order_id):
    """Step 4 — Collect and record the surgery booking deposit."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'visit', 'procedure_master'),
        pk=order_id,
    )

    if order.status != SurgeryOrder.Status.BOOKING_DEPOSIT:
        messages.error(request, 'Booking deposit is not pending for this order.')
        return redirect('surgery_order_detail', order_id=order_id)

    if request.method == 'POST':
        try:
            amount_raw = request.POST.get('amount', '').strip()
            if not amount_raw:
                raise ValueError('Amount is required.')
            amount   = Decimal(amount_raw)
            if amount <= 0:
                raise ValueError('Amount must be greater than zero.')
            method    = request.POST.get('payment_method', Payment.Method.CASH)
            reference = request.POST.get('reference_number', '').strip()
            notes     = request.POST.get('notes', '').strip()

            with transaction.atomic():
                # Reuse the invoice already created when patient agreed, or create fresh
                if order.booking_deposit_invoice_id:
                    invoice = order.booking_deposit_invoice
                    invoice.status       = Invoice.Status.PAID
                    invoice.total_amount = amount
                    invoice.paid_amount  = amount
                    invoice.save(update_fields=['status', 'total_amount', 'paid_amount'])
                    item = order.booking_deposit_invoice_item
                    if item:
                        item.unit_price      = amount
                        item.payment_status  = InvoiceItem.PaymentStatus.PAID
                        item.paid_amount     = amount
                        item.save(update_fields=['unit_price', 'payment_status', 'paid_amount'])
                    else:
                        item = InvoiceItem.objects.create(
                            invoice=invoice,
                            description=f'Surgery Booking Deposit — {order.planned_procedure}',
                            service_type=InvoiceItem.ServiceType.SURGERY_BOOKING,
                            quantity=1,
                            unit_price=amount,
                            payment_status=InvoiceItem.PaymentStatus.PAID,
                            paid_amount=amount,
                        )
                else:
                    invoice = Invoice.objects.create(
                        patient=order.patient,
                        visit=order.visit,
                        created_by=request.user,
                        status=Invoice.Status.PAID,
                        total_amount=amount,
                        paid_amount=amount,
                        notes=f'Surgery Booking Deposit — {order.order_number}',
                    )
                    item = InvoiceItem.objects.create(
                        invoice=invoice,
                        description=f'Surgery Booking Deposit — {order.planned_procedure}',
                        service_type=InvoiceItem.ServiceType.SURGERY_BOOKING,
                        quantity=1,
                        unit_price=amount,
                        payment_status=InvoiceItem.PaymentStatus.PAID,
                        paid_amount=amount,
                    )

                Payment.objects.create(
                    invoice=invoice,
                    amount=amount,
                    payment_method=method,
                    reference_number=reference,
                    received_by=request.user,
                    payment_date=timezone.localdate(),
                    notes=notes,
                )
                order.booking_deposit_amount       = amount
                order.booking_deposit_invoice      = invoice
                order.booking_deposit_invoice_item = item
                order.booking_deposit_paid         = True
                order.booking_deposit_paid_at      = timezone.now()
                order.status                       = SurgeryOrder.Status.APPROVED
                order.save()

            log_action(
                request.user, AuditLog.Action.PAYMENT, AuditLog.Module.SURGERY,
                object_type='SurgeryOrder', object_id=order.pk,
                object_repr=order.order_number,
                description=f'Surgery booking deposit ETB {amount:,.2f} paid for {order.order_number} ({order.patient.full_name})',
                request=request,
            )
            messages.success(request, f'Booking deposit of ETB {amount:,.2f} recorded. Order ready for scheduling.')
            return redirect('surgery_booking_deposit_receipt', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/booking_deposit_form.html', {
        'order':   order,
        'methods': Payment.Method.choices,
    })


@hms_permission_required('core.process_surgery_booking_deposit')
def surgery_booking_deposit_receipt(request, order_id):
    """Printable booking deposit receipt."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related(
            'patient', 'procedure_master', 'booking_deposit_invoice',
            'booking_deposit_invoice_item',
        ),
        pk=order_id,
    )
    payment = None
    if order.booking_deposit_invoice:
        payment = order.booking_deposit_invoice.payments.order_by('-created_at').first()
    return render(request, 'surgery/booking_deposit_receipt.html', {
        'order':   order,
        'payment': payment,
    })


@hms_permission_required('core.initiate_surgery_admission')
def surgery_initiate_admission(request, order_id):
    """Step 6 — Create an AdmissionRequest (source='or') linked to a scheduled
    surgery order, so the admission pipeline can assign a bed and collect the
    inpatient deposit."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'visit', 'surgeon', 'department'),
        pk=order_id,
    )

    if order.status not in (SurgeryOrder.Status.SCHEDULED, SurgeryOrder.Status.APPROVED):
        messages.error(request, 'Admission can only be initiated for a Scheduled or Approved order.')
        return redirect('surgery_order_detail', order_id=order_id)

    if not order.visit_id:
        messages.error(request, 'Surgery order must be linked to a patient visit before initiating admission.')
        return redirect('surgery_order_detail', order_id=order_id)

    if order.surgery_admission_request_id:
        messages.info(request, 'An admission request already exists for this order.')
        return redirect('admission_request_detail', pk=order.surgery_admission_request_id)

    departments = Department.objects.filter(is_active=True).order_by('name')
    wards       = Ward.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        try:
            diagnosis = request.POST.get('admission_diagnosis', '').strip()
            reason    = request.POST.get('reason_for_admission', '').strip()
            dept_id   = request.POST.get('admitting_department')
            priority  = request.POST.get('priority', AdmissionRequest.Priority.ROUTINE)
            los       = request.POST.get('expected_los_days') or None
            notes     = request.POST.get('notes', '').strip()

            if not diagnosis:
                raise ValueError('Admission diagnosis is required.')
            if not reason:
                reason = f'Surgical case: {order.planned_procedure}'

            with transaction.atomic():
                adm_req = AdmissionRequest.objects.create(
                    visit=order.visit,
                    patient=order.patient,
                    admission_diagnosis=diagnosis,
                    reason_for_admission=reason,
                    admitting_department_id=dept_id or None,
                    admitting_doctor=order.surgeon,
                    priority=priority,
                    expected_los_days=int(los) if los else None,
                    request_source=AdmissionRequest.Source.OR,
                    requested_by=request.user,
                    status=AdmissionRequest.Status.PENDING,
                    notes=notes,
                )
                order.surgery_admission_request = adm_req
                order.status                    = SurgeryOrder.Status.AWAITING_ADMISSION
                order.save(update_fields=['surgery_admission_request', 'status'])

            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.ADMISSION,
                object_type='AdmissionRequest', object_id=adm_req.pk,
                object_repr=order.patient.full_name,
                description=(
                    f'Admission request created from surgery order {order.order_number} '
                    f'for {order.patient.full_name} (source: OR)'
                ),
                request=request,
            )
            messages.success(request, 'Admission request created and sent for review.')
            return redirect('admission_request_detail', pk=adm_req.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/initiate_admission_form.html', {
        'order':       order,
        'departments': departments,
        'wards':       wards,
        'priorities':  AdmissionRequest.Priority.choices,
    })


# ── Surgery Pre-Deposit (actual surgery deposit, day before / day of surgery) ──

@hms_permission_required('core.collect_surgery_pre_deposit')
def surgery_pre_deposit(request, order_id):
    """Collect the actual surgery deposit when the patient arrives.
    Covers the full estimated surgery cost. Credit patients are recorded
    with no upfront payment and billed at discharge."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'visit', 'procedure_master'),
        pk=order_id,
    )

    # Already has a pre-deposit?
    if hasattr(order, 'pre_deposit'):
        messages.info(request, 'A pre-deposit has already been recorded for this order.')
        return redirect('surgery_order_detail', order_id=order_id)

    eligible_statuses = (
        SurgeryOrder.Status.SCHEDULED,
        SurgeryOrder.Status.AWAITING_ADMISSION,
        SurgeryOrder.Status.APPROVED,
        SurgeryOrder.Status.PATIENT_PREPARED,
    )
    if order.status not in eligible_statuses:
        messages.error(request, 'Order is not in a state that accepts a surgery deposit.')
        return redirect('surgery_order_detail', order_id=order_id)

    if request.method == 'POST':
        is_credit = request.POST.get('payment_type') == 'credit'
        notes     = request.POST.get('notes', '').strip()

        try:
            with transaction.atomic():
                if is_credit:
                    credit_limit = Decimal(request.POST.get('credit_limit', '0').strip() or '0')
                    pre_dep = SurgeryPreDeposit.objects.create(
                        surgery_order      = order,
                        patient            = order.patient,
                        is_credit          = True,
                        credit_limit       = credit_limit,
                        collected_by       = request.user,
                        credit_approved_by = request.user,
                        credit_approved_at = timezone.now(),
                        notes              = notes,
                    )
                    order.status = SurgeryOrder.Status.PATIENT_PREPARED
                    order.save(update_fields=['status'])
                    log_action(
                        request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                        object_type='SurgeryPreDeposit', object_id=pre_dep.pk,
                        object_repr=order.order_number,
                        description=f'Credit approved for surgery {order.order_number} — {order.patient.full_name}',
                        request=request,
                    )
                    messages.success(request, f'Credit arrangement approved for {order.patient.full_name}. Patient is ready.')
                else:
                    amount_raw = request.POST.get('amount', '').strip()
                    if not amount_raw:
                        raise ValueError('Deposit amount is required.')
                    amount    = Decimal(amount_raw)
                    if amount <= 0:
                        raise ValueError('Amount must be greater than zero.')
                    method    = request.POST.get('payment_method', Payment.Method.CASH)
                    reference = request.POST.get('reference_number', '').strip()

                    invoice = Invoice.objects.create(
                        patient      = order.patient,
                        visit        = order.visit,
                        created_by   = request.user,
                        status       = Invoice.Status.PAID,
                        total_amount = amount,
                        paid_amount  = amount,
                        notes        = f'Surgery Pre-Deposit — {order.order_number}',
                    )
                    item = InvoiceItem.objects.create(
                        invoice        = invoice,
                        description    = f'Surgery Deposit — {order.planned_procedure}',
                        service_type   = InvoiceItem.ServiceType.SURGERY_DEPOSIT,
                        quantity       = 1,
                        unit_price     = amount,
                        payment_status = InvoiceItem.PaymentStatus.PAID,
                        paid_amount    = amount,
                    )
                    Payment.objects.create(
                        invoice          = invoice,
                        amount           = amount,
                        payment_method   = method,
                        reference_number = reference,
                        received_by      = request.user,
                        payment_date     = timezone.localdate(),
                        notes            = notes,
                    )
                    pre_dep = SurgeryPreDeposit.objects.create(
                        surgery_order    = order,
                        patient          = order.patient,
                        deposit_amount   = amount,
                        payment_method   = method,
                        reference_number = reference,
                        invoice          = invoice,
                        invoice_item     = item,
                        collected_by     = request.user,
                        notes            = notes,
                    )
                    order.status = SurgeryOrder.Status.PATIENT_PREPARED
                    order.save(update_fields=['status'])
                    log_action(
                        request.user, AuditLog.Action.PAYMENT, AuditLog.Module.SURGERY,
                        object_type='SurgeryPreDeposit', object_id=pre_dep.pk,
                        object_repr=order.order_number,
                        description=f'Surgery pre-deposit ETB {amount:,.2f} collected — {order.order_number} ({order.patient.full_name})',
                        request=request,
                    )
                    messages.success(request, f'Surgery deposit of ETB {amount:,.2f} collected. Patient is ready.')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
            return render(request, 'surgery/pre_deposit_form.html', {
                'order': order, 'methods': Payment.Method.choices,
            })

        return redirect('surgery_pre_deposit_receipt', order_id=order_id)

    return render(request, 'surgery/pre_deposit_form.html', {
        'order':   order,
        'methods': Payment.Method.choices,
    })


@hms_permission_required('core.collect_surgery_pre_deposit')
def surgery_pre_deposit_receipt(request, order_id):
    """Printable receipt for the surgery pre-deposit."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'procedure_master', 'surgeon'),
        pk=order_id,
    )
    pre_dep = get_object_or_404(SurgeryPreDeposit, surgery_order=order)
    payment = None
    if pre_dep.invoice:
        payment = pre_dep.invoice.payments.order_by('-created_at').first()
    return render(request, 'surgery/pre_deposit_receipt.html', {
        'order':   order,
        'pre_dep': pre_dep,
        'payment': payment,
    })


@hms_permission_required('core.settle_surgery_account')
def surgery_deposit_settlement(request, order_id):
    """Discharge settlement: reconcile pre-deposit against all charges.
    Surplus → refund. Deficit → collect. Credit patients → full bill at discharge."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'visit', 'procedure_master', 'surgeon'),
        pk=order_id,
    )
    pre_dep = get_object_or_404(SurgeryPreDeposit, surgery_order=order)

    if pre_dep.status == SurgeryPreDeposit.Status.SETTLED:
        return redirect('surgery_deposit_settlement_receipt', order_id=order_id)

    # Gather all invoice items for this visit, excluding the deposit itself
    charge_items = []
    if order.visit_id:
        invoices = Invoice.objects.filter(visit_id=order.visit_id).prefetch_related('items')
        for inv in invoices:
            for it in inv.items.all():
                if it.service_type not in (
                    InvoiceItem.ServiceType.SURGERY_DEPOSIT,
                    InvoiceItem.ServiceType.SURGERY_BOOKING,
                ):
                    charge_items.append(it)

    total_charges = sum(it.total for it in charge_items)
    deposit       = pre_dep.deposit_amount if not pre_dep.is_credit else Decimal('0')
    net           = deposit - total_charges
    balance_refund = max(net, Decimal('0'))
    balance_due    = max(-net, Decimal('0'))

    if request.method == 'POST':
        settlement_notes = request.POST.get('settlement_notes', '').strip()
        additional_payment_raw = request.POST.get('additional_payment', '0').strip()
        try:
            additional_payment = Decimal(additional_payment_raw or '0')
        except Exception:
            additional_payment = Decimal('0')

        try:
            with transaction.atomic():
                pre_dep.total_charges    = total_charges
                pre_dep.balance_refund   = balance_refund
                pre_dep.balance_due      = balance_due
                pre_dep.settled_at       = timezone.now()
                pre_dep.settled_by       = request.user
                pre_dep.settlement_notes = settlement_notes
                pre_dep.status           = SurgeryPreDeposit.Status.SETTLED
                pre_dep.save()

                order.status                 = SurgeryOrder.Status.DISCHARGED
                order.discharged_surgery_at  = timezone.now()
                order.save(update_fields=['status', 'discharged_surgery_at'])

                log_action(
                    request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                    object_type='SurgeryPreDeposit', object_id=pre_dep.pk,
                    object_repr=order.order_number,
                    description=(
                        f'Surgery account settled — {order.order_number} ({order.patient.full_name}). '
                        f'Total charges: ETB {total_charges:,.2f}. '
                        f'{"Refund" if balance_refund else "Due"}: ETB {(balance_refund or balance_due):,.2f}'
                    ),
                    request=request,
                )
            messages.success(request, 'Surgery account settled successfully.')
            return redirect('surgery_deposit_settlement_receipt', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/deposit_settlement.html', {
        'order':         order,
        'pre_dep':       pre_dep,
        'charge_items':  charge_items,
        'total_charges': total_charges,
        'deposit':       deposit,
        'balance_refund': balance_refund,
        'balance_due':    balance_due,
    })


@hms_permission_required('core.settle_surgery_account')
def surgery_deposit_settlement_receipt(request, order_id):
    """Printable discharge settlement receipt."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'procedure_master', 'surgeon'),
        pk=order_id,
    )
    pre_dep = get_object_or_404(SurgeryPreDeposit, surgery_order=order)
    charge_items = []
    if order.visit_id:
        invoices = Invoice.objects.filter(visit_id=order.visit_id).prefetch_related('items')
        for inv in invoices:
            for it in inv.items.all():
                if it.service_type not in (
                    InvoiceItem.ServiceType.SURGERY_DEPOSIT,
                    InvoiceItem.ServiceType.SURGERY_BOOKING,
                ):
                    charge_items.append(it)
    return render(request, 'surgery/deposit_settlement_receipt.html', {
        'order':       order,
        'pre_dep':     pre_dep,
        'charge_items': charge_items,
    })


# ── Pre-Operative Checklist ────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def preop_checklist(request, order_id):
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'procedure_master', 'surgeon'),
        pk=order_id,
    )
    checklist, _ = SurgeryPreOpChecklist.objects.get_or_create(
        surgery_order=order,
        defaults={'created_by': request.user},
    )

    if request.method == 'POST':
        bool_fields = [
            'cbc_done', 'coagulation_done', 'blood_group_done', 'blood_sugar_done',
            'rft_done', 'lft_done', 'electrolytes_done',
            'cxr_done', 'ecg_done', 'other_imaging_done',
            'anesthesia_clearance', 'medical_clearance', 'surgical_consent_signed',
            'anesthesia_consent_signed',
            'blood_required', 'blood_available',
            'npo_confirmed', 'iv_access', 'site_marked', 'patient_identified',
            'pre_medication_given', 'antibiotic_prophylaxis', 'dvt_prophylaxis',
            'allergies_reviewed', 'implants_available',
            'who_sign_in_done', 'who_time_out_done', 'who_sign_out_done',
            'emergency_override',
        ]
        for f in bool_fields:
            setattr(checklist, f, request.POST.get(f) in ('on', '1', 'true'))

        checklist.units_prepared = int(request.POST.get('units_prepared') or 0)
        checklist.override_reason = request.POST.get('override_reason', '').strip()
        checklist.notes = request.POST.get('notes', '').strip()
        checklist.updated_by = request.user

        if checklist.emergency_override and not checklist.override_by:
            checklist.override_by = request.user
            checklist.override_at = timezone.now()

        checklist.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryPreOpChecklist', object_id=checklist.pk,
            object_repr=str(checklist),
            description=f'Pre-op checklist updated for {order.order_number} ({checklist.completion_percent}% complete)',
            request=request,
        )
        messages.success(request, f'Pre-op checklist saved ({checklist.completion_percent}% complete).')
        return redirect('surgery_order_detail', order_id=order.pk)

    return render(request, 'surgery/preop_checklist.html', {
        'order':     order,
        'checklist': checklist,
    })


# ── PACU / Recovery Room Record ────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def pacu_record_create(request, order_id):
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'procedure_master', 'surgeon'),
        pk=order_id,
    )

    # Get existing current record or start fresh
    existing = SurgeryPACURecord.objects.filter(surgery_order=order, is_current=True).first()

    if request.method == 'POST':
        action = request.POST.get('action', 'draft')

        def _pdts(name):
            v = request.POST.get(name, '').strip()
            if not v:
                return None
            try:
                parsed = dt.fromisoformat(v)
                return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
            except ValueError:
                return None

        def _psmall(name):
            try:
                return int(request.POST.get(name, ''))
            except (ValueError, TypeError):
                return None

        def _pdec_local(name):
            try:
                return Decimal(request.POST.get(name, ''))
            except (InvalidOperation, TypeError, ValueError):
                return None

        fields = {
            'arrival_time':          _pdts('arrival_time'),
            'arrival_bp_systolic':   _psmall('arrival_bp_systolic'),
            'arrival_bp_diastolic':  _psmall('arrival_bp_diastolic'),
            'arrival_pulse':         _psmall('arrival_pulse'),
            'arrival_rr':            _psmall('arrival_rr'),
            'arrival_temp':          _pdec_local('arrival_temp'),
            'arrival_spo2':          _psmall('arrival_spo2'),
            'arrival_pain_score':    _psmall('arrival_pain_score'),
            'consciousness_level':   request.POST.get('consciousness_level', ''),
            'airway_status':         request.POST.get('airway_status', '').strip(),
            'o2_delivery_method':    request.POST.get('o2_delivery_method', '').strip(),
            'o2_flow_rate':          request.POST.get('o2_flow_rate', '').strip(),
            'wound_condition':       request.POST.get('wound_condition', '').strip(),
            'drain_type':            request.POST.get('drain_type', '').strip(),
            'drain_output_ml':       _psmall('drain_output_ml'),
            'bleeding_notes':        request.POST.get('bleeding_notes', '').strip(),
            'analgesics_given':      request.POST.get('analgesics_given', '').strip(),
            'antiemetics_given':     request.POST.get('antiemetics_given', '').strip(),
            'iv_fluids_given':       request.POST.get('iv_fluids_given', '').strip(),
            'other_medications':     request.POST.get('other_medications', '').strip(),
            'aldrete_activity':      _psmall('aldrete_activity'),
            'aldrete_respiration':   _psmall('aldrete_respiration'),
            'aldrete_circulation':   _psmall('aldrete_circulation'),
            'aldrete_consciousness': _psmall('aldrete_consciousness'),
            'aldrete_spo2':          _psmall('aldrete_spo2'),
            'discharge_time':        _pdts('discharge_time'),
            'discharge_destination': request.POST.get('discharge_destination', ''),
            'discharge_criteria_met': request.POST.get('discharge_criteria_met') in ('on', '1'),
            'pacu_duration_minutes': _psmall('pacu_duration_minutes'),
            'complications':         request.POST.get('complications', '').strip(),
            'monitoring_notes':      request.POST.get('monitoring_notes', '').strip(),
            'notes':                 request.POST.get('notes', '').strip(),
        }

        with transaction.atomic():
            if existing and existing.is_finalized:
                # Create new version
                existing.is_current = False
                existing.save(update_fields=['is_current'])
                record = SurgeryPACURecord(
                    surgery_order=order,
                    version=existing.version + 1,
                    supersedes=existing,
                    is_current=True,
                    created_by=request.user,
                )
            elif existing:
                record = existing
                record.updated_by = request.user
            else:
                record = SurgeryPACURecord(
                    surgery_order=order,
                    is_current=True,
                    created_by=request.user,
                )

            for attr, val in fields.items():
                setattr(record, attr, val)

            if action == 'finalize':
                record.doc_status   = SurgeryPACURecord.DocStatus.FINALIZED
                record.finalized_by = request.user
                record.finalized_at = timezone.now()
                record.signature_name = request.user.get_full_name() or request.user.username
            else:
                record.doc_status = SurgeryPACURecord.DocStatus.DRAFT

            record.save()

        log_action(
            request.user, AuditLog.Action.CREATE if not existing else AuditLog.Action.UPDATE,
            AuditLog.Module.SURGERY,
            object_type='SurgeryPACURecord', object_id=record.pk,
            object_repr=str(record),
            description=f'PACU record {"finalized" if action == "finalize" else "saved as draft"} for {order.order_number}',
            request=request,
        )
        msg = 'PACU record finalized.' if action == 'finalize' else 'PACU record saved as draft.'
        messages.success(request, msg)
        return redirect('surgery_order_detail', order_id=order.pk)

    return render(request, 'surgery/pacu_record.html', {
        'order':  order,
        'record': existing,
        'consciousness_choices':   SurgeryPACURecord.Consciousness.choices,
        'destination_choices':     SurgeryPACURecord.DischargeDestination.choices,
    })


# ── Surgery Patient Journey API ────────────────────────────────────────────────

import json as _json
from django.http import JsonResponse

@hms_permission_required('core.read_surgery')
def surgery_journey_api(request, order_id):
    """Return the patient's surgical journey as a JSON list of stage objects."""
    order = get_object_or_404(
        SurgeryOrder.objects.select_related('patient', 'surgeon', 'procedure_master'),
        pk=order_id,
    )

    STATUS_ORDER = [
        'ordered', 'pending_review', 'awaiting_decision', 'booking_deposit',
        'approved', 'scheduled', 'awaiting_admission', 'patient_prepared',
        'in_or', 'recovery', 'post_op', 'completed', 'discharged',
    ]

    STAGE_META = {
        'ordered':            {'label': 'Surgery Ordered',           'icon': '📋', 'color': 'blue'},
        'pending_review':     {'label': 'Pending Review',            'icon': '🔍', 'color': 'yellow'},
        'awaiting_decision':  {'label': 'Patient Counselling',       'icon': '💬', 'color': 'purple'},
        'booking_deposit':    {'label': 'Booking Deposit',           'icon': '💳', 'color': 'indigo'},
        'approved':           {'label': 'Approved & Scheduled',      'icon': '✅', 'color': 'green'},
        'scheduled':          {'label': 'Surgery Scheduled',         'icon': '📅', 'color': 'cyan'},
        'awaiting_admission': {'label': 'Pre-Op Assessment',         'icon': '🔬', 'color': 'orange'},
        'patient_prepared':   {'label': 'Admitted & Prepared',       'icon': '🏥', 'color': 'teal'},
        'in_or':              {'label': 'In Operating Room',         'icon': '🏨', 'color': 'red'},
        'recovery':           {'label': 'Recovery (PACU)',           'icon': '💊', 'color': 'amber'},
        'post_op':            {'label': 'Ward (Post-Op)',            'icon': '🛏️', 'color': 'sky'},
        'completed':          {'label': 'Surgery Completed',         'icon': '🎯', 'color': 'emerald'},
        'discharged':         {'label': 'Discharged',                'icon': '🏠', 'color': 'slate'},
    }

    current_idx = STATUS_ORDER.index(order.status) if order.status in STATUS_ORDER else 0

    stages = []
    for i, s in enumerate(STATUS_ORDER):
        meta = STAGE_META.get(s, {'label': s, 'icon': '•', 'color': 'slate'})
        stages.append({
            'status':    s,
            'label':     meta['label'],
            'icon':      meta['icon'],
            'color':     meta['color'],
            'state':     'done' if i < current_idx else ('current' if i == current_idx else 'pending'),
        })

    return JsonResponse({'stages': stages, 'current': order.status})
