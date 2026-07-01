from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Department, Invoice, InvoiceItem, OperativeNote, ORRoom,
    Patient, ProcedureCategory, ProcedureMaster, SurgeryAnesthesiaRecord,
    SurgeryConsumable, SurgeryOrder, SurgerySchedule, Visit,
)

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


# ── Surgery Dashboard ─────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_dashboard(request):
    today = timezone.localdate()

    orders = SurgeryOrder.objects.select_related('patient', 'surgeon', 'department')

    # KPIs
    total_orders   = orders.count()
    pending        = orders.filter(status__in=['ordered', 'pending_review']).count()
    approved       = orders.filter(status='approved').count()
    scheduled_today = SurgerySchedule.objects.filter(scheduled_date=today).count()
    in_or          = orders.filter(status='in_or').count()
    completed_month = orders.filter(
        status='completed',
        completed_at__year=today.year,
        completed_at__month=today.month,
    ).count()
    cancelled_month = orders.filter(
        cancelled_at__year=today.year,
        cancelled_at__month=today.month,
    ).count()

    # Today's schedule
    todays_schedule = (
        SurgerySchedule.objects
        .filter(scheduled_date=today)
        .select_related('surgery_order__patient', 'surgery_order__surgeon', 'or_room')
        .order_by('scheduled_start_time')
    )

    # Emergency/urgent orders needing attention
    urgent = orders.filter(
        priority__in=['emergency', 'urgent'],
        status__in=['ordered', 'pending_review', 'approved'],
    ).order_by('priority', '-ordered_at')[:10]

    # Recent orders
    recent = orders.order_by('-ordered_at')[:10]

    return render(request, 'surgery/dashboard.html', {
        'total_orders':    total_orders,
        'pending':         pending,
        'approved':        approved,
        'scheduled_today': scheduled_today,
        'in_or':           in_or,
        'completed_month': completed_month,
        'cancelled_month': cancelled_month,
        'todays_schedule': todays_schedule,
        'urgent':          urgent,
        'recent':          recent,
        'today':           today,
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

        order = SurgeryOrder(
            patient          = pt,
            visit            = vt,
            procedure_master = proc,
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
        )
        try:
            order.full_clean()
            order.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                object_type='SurgeryOrder', object_id=order.pk,
                object_repr=order.order_number,
                description=f'Surgery order created: {order.planned_procedure} for {pt.full_name} ({order.priority})',
                extra_data={'priority': order.priority, 'patient': pt.full_name},
                severity=AuditLog.Severity.WARNING if order.priority == 'emergency' else AuditLog.Severity.INFO,
                request=request,
            )
            messages.success(request, f'Surgery order {order.order_number} created successfully.')
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
            'approved_by', 'cancelled_by', 'invoice',
        ),
        pk=order_id,
    )

    schedule          = getattr(order, 'schedule', None)
    anesthesia_record = getattr(order, 'surgery_anesthesia_record', None)
    operative_note    = getattr(order, 'operative_note', None)
    consumables       = order.consumables.select_related('recorded_by').all()
    consumable_total  = consumables.aggregate(t=Sum('total_cost'))['t'] or 0

    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.SURGERY,
        object_type='SurgeryOrder', object_id=order.pk,
        object_repr=order.order_number,
        description=f'Surgery order viewed: {order.order_number} ({order.patient.full_name})',
        request=request,
    )

    return render(request, 'surgery/order_detail.html', {
        'order':            order,
        'schedule':         schedule,
        'anesthesia_record': anesthesia_record,
        'operative_note':   operative_note,
        'consumables':      consumables,
        'consumable_total': consumable_total,
        'status_choices':   SurgeryOrder.Status.choices,
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

    elif action == 'patient_prepared' and order.status == SurgeryOrder.Status.SCHEDULED:
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

    elif action == 'post_op' and order.status == SurgeryOrder.Status.COMPLETED:
        order.status = SurgeryOrder.Status.POST_OP
        order.save()
        messages.success(request, 'Order moved to post-operative care.')

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


@hms_permission_required('core.manage_or_schedule')
def or_room_create(request):
    if request.method == 'POST':
        p = request.POST
        try:
            room = ORRoom.objects.create(
                name      = p['name'].strip(),
                room_type = p.get('room_type', ORRoom.RoomType.GENERAL),
                location  = p.get('location', '').strip(),
                capacity  = int(p.get('capacity', 1) or 1),
                equipment = p.get('equipment', '').strip(),
                notes     = p.get('notes', '').strip(),
                is_active = True,
            )
            messages.success(request, f'OR Room "{room.name}" created.')
            return redirect('or_room_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/or_room_form.html', {
        'room_types': ORRoom.RoomType.choices,
        'action':     'Create',
    })


@hms_permission_required('core.manage_or_schedule')
def or_room_edit(request, room_id):
    room = get_object_or_404(ORRoom, pk=room_id)
    if request.method == 'POST':
        p = request.POST
        try:
            room.name      = p['name'].strip()
            room.room_type = p.get('room_type', room.room_type)
            room.location  = p.get('location', '').strip()
            room.capacity  = int(p.get('capacity', room.capacity) or room.capacity)
            room.equipment = p.get('equipment', '').strip()
            room.notes     = p.get('notes', '').strip()
            room.is_active = bool(p.get('is_active'))
            room.save()
            messages.success(request, f'OR Room "{room.name}" updated.')
            return redirect('or_room_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/or_room_form.html', {
        'room':       room,
        'room_types': ORRoom.RoomType.choices,
        'action':     'Edit',
    })


# ── Anesthesia Record (Surgery) ───────────────────────────────────────────────

@hms_permission_required('core.write_surgery_anesthesia')
def surgery_anesthesia_create(request, order_id):
    order    = get_object_or_404(SurgeryOrder, pk=order_id)
    existing = getattr(order, 'surgery_anesthesia_record', None)

    if request.method == 'POST':
        p = request.POST
        data = dict(
            surgery_order    = order,
            anesthesiologist = request.user,
            asa_classification   = p.get('asa_classification', ''),
            pre_assessment_notes = p.get('pre_assessment_notes', '').strip(),
            known_allergies      = p.get('known_allergies', '').strip(),
            pre_medication       = p.get('pre_medication', '').strip(),
            anesthesia_technique = p.get('anesthesia_technique', '').strip(),
            induction_agent      = p.get('induction_agent', '').strip(),
            maintenance_agent    = p.get('maintenance_agent', '').strip(),
            airway_management    = p.get('airway_management', '').strip(),
            monitoring_notes     = p.get('monitoring_notes', '').strip(),
            intraop_complications = p.get('intraop_complications', '').strip(),
            post_anesthesia_notes = p.get('post_anesthesia_notes', '').strip(),
            pacu_duration_minutes = p.get('pacu_duration_minutes') or None,
            pacu_complications    = p.get('pacu_complications', '').strip(),
        )
        try:
            if existing:
                for k, v in data.items():
                    if k != 'surgery_order':
                        setattr(existing, k, v)
                existing.save()
                rec = existing
            else:
                rec = SurgeryAnesthesiaRecord.objects.create(**data)

            log_action(
                request.user, AuditLog.Action.CREATE if not existing else AuditLog.Action.UPDATE,
                AuditLog.Module.SURGERY,
                object_type='SurgeryAnesthesiaRecord', object_id=rec.pk,
                object_repr=order.order_number,
                description=f'Anesthesia record {"updated" if existing else "created"} for {order.order_number}',
                request=request,
            )
            messages.success(request, 'Anesthesia record saved.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/anesthesia_form.html', {
        'order':  order,
        'record': existing,
        'asa_choices': SurgeryAnesthesiaRecord.ASA.choices,
    })


# ── Operative Note ────────────────────────────────────────────────────────────

@hms_permission_required('core.write_operative_note')
def operative_note_create(request, order_id):
    order    = get_object_or_404(SurgeryOrder, pk=order_id)
    existing = getattr(order, 'operative_note', None)

    if request.method == 'POST':
        p = request.POST
        data = dict(
            surgery_order       = order,
            procedure_performed = p.get('procedure_performed', '').strip() or order.planned_procedure,
            start_time          = p.get('start_time') or None,
            end_time            = p.get('end_time') or None,
            pre_op_diagnosis    = p.get('pre_op_diagnosis', '').strip(),
            post_op_diagnosis   = p.get('post_op_diagnosis', '').strip(),
            anesthesia_type     = p.get('anesthesia_type', '').strip(),
            assistant_surgeons  = p.get('assistant_surgeons', '').strip(),
            findings            = p.get('findings', '').strip(),
            procedure_steps     = p.get('procedure_steps', '').strip(),
            complications       = p.get('complications', '').strip(),
            blood_loss_ml       = p.get('blood_loss_ml') or None,
            urine_output_ml     = p.get('urine_output_ml') or None,
            specimens_collected  = p.get('specimens_collected', '').strip(),
            implants_used        = p.get('implants_used', '').strip(),
            drains_placed        = p.get('drains_placed', '').strip(),
            wound_closure        = p.get('wound_closure', '').strip(),
            post_op_instructions = p.get('post_op_instructions', '').strip(),
            authored_by          = request.user,
        )
        try:
            if existing:
                for k, v in data.items():
                    if k not in ('surgery_order', 'authored_by'):
                        setattr(existing, k, v)
                existing.save()
                note = existing
            else:
                note = OperativeNote.objects.create(**data)

            log_action(
                request.user,
                AuditLog.Action.UPDATE if existing else AuditLog.Action.CREATE,
                AuditLog.Module.SURGERY,
                object_type='OperativeNote', object_id=note.pk,
                object_repr=order.order_number,
                description=f'Operative note {"updated" if existing else "created"} for {order.order_number}',
                request=request,
            )
            messages.success(request, 'Operative note saved.')
            return redirect('surgery_order_detail', order_id=order_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'surgery/operative_note_form.html', {
        'order':  order,
        'note':   existing,
    })


# ── Surgery Consumables ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_surgery_consumables')
@require_POST
def surgery_consumable_add(request, order_id):
    order = get_object_or_404(SurgeryOrder, pk=order_id)
    p = request.POST
    try:
        qty = p.get('quantity', 1) or 1
        unit_cost = p.get('unit_cost', 0) or 0
        SurgeryConsumable.objects.create(
            surgery_order = order,
            item_type     = p.get('item_type', SurgeryConsumable.ItemType.SUPPLY),
            item_name     = p['item_name'].strip(),
            quantity      = qty,
            unit          = p.get('unit', 'unit').strip() or 'unit',
            batch_number  = p.get('batch_number', '').strip(),
            expiry_date   = p.get('expiry_date') or None,
            unit_cost     = unit_cost,
            recorded_by   = request.user,
        )
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
            object_type='SurgeryConsumable', object_id=order.pk,
            object_repr=order.order_number,
            description=f'Consumable added: {p["item_name"]} × {qty} for {order.order_number}',
            request=request,
        )
        messages.success(request, f'Consumable "{p["item_name"]}" recorded.')
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

        items = []
        if pm:
            if pm.procedure_price:
                items.append(('Procedure Fee', 'SURGERY', pm.procedure_price))
            if pm.surgeon_fee:
                items.append(('Surgeon Fee', 'SURGERY', pm.surgeon_fee))
            if pm.anesthesia_fee:
                items.append(('Anesthesia Fee', 'SURGERY', pm.anesthesia_fee))
            if pm.facility_fee:
                items.append(('Facility / OR Fee', 'SURGERY', pm.facility_fee))
            if pm.consumable_charges:
                items.append(('Surgical Consumables', 'SURGERY', pm.consumable_charges))

        # Add tracked consumables
        for con in order.consumables.all():
            if con.total_cost:
                items.append((f'Consumable: {con.item_name}', 'SURGERY', con.total_cost))

        total = Decimal('0')
        for desc, svc, price in items:
            price = Decimal(str(price))
            InvoiceItem.objects.create(
                invoice      = invoice,
                description  = desc,
                service_type = svc,
                quantity     = Decimal('1'),
                unit_price   = price,
                total        = price,
            )
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
    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')

    qs = SurgeryOrder.objects.all()
    if date_from:
        qs = qs.filter(ordered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(ordered_at__date__lte=date_to)

    surgeon_stats = (
        qs
        .values('surgeon__id', 'surgeon__first_name', 'surgeon__last_name')
        .annotate(
            total       = Count('id'),
            completed   = Count('id', filter=Q(status='completed')),
            cancelled   = Count('id', filter=Q(status='cancelled')),
            emergency   = Count('id', filter=Q(priority='emergency')),
        )
        .order_by('-total')
    )

    return render(request, 'surgery/reports/surgeon_performance.html', {
        'surgeon_stats': surgeon_stats,
        'date_from':     date_from,
        'date_to':       date_to,
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
