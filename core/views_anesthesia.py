from datetime import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import hms_permission_required
from .models import AnesthesiaRecord, ProcedureOrder, Visit


# ── Anesthesia Dashboard ──────────────────────────────────────────────────────

@hms_permission_required('core.read_anesthesia_record')
def anesthesia_dashboard(request):
    today = timezone.localdate()

    # Stats
    records_today = AnesthesiaRecord.objects.filter(created_at__date=today)
    records_today_count = records_today.count()

    # Pending procedures (procedure orders with no anesthesia record)
    pending_procedures = ProcedureOrder.objects.filter(
        status__in=['Scheduled', 'In Progress'],
        anesthesia_records__isnull=True,
    ).select_related('visit__patient', 'visit__department').order_by('-ordered_at')
    pending_count = pending_procedures.count()

    # This month stats
    first_of_month = today.replace(day=1)
    month_count = AnesthesiaRecord.objects.filter(created_at__date__gte=first_of_month).count()

    # Today's cases list
    todays_cases = (
        AnesthesiaRecord.objects
        .filter(created_at__date=today)
        .select_related('visit__patient', 'visit__department', 'procedure_order', 'anesthesiologist')
        .order_by('-created_at')
    )

    # Recent records (last 10)
    recent_records = (
        AnesthesiaRecord.objects
        .exclude(created_at__date=today)
        .select_related('visit__patient', 'procedure_order', 'anesthesiologist')
        .order_by('-created_at')[:10]
    )

    return render(request, 'anesthesia/dashboard.html', {
        'today': today,
        'records_today_count': records_today_count,
        'pending_count': pending_count,
        'month_count': month_count,
        'todays_cases': todays_cases,
        'recent_records': recent_records,
        'pending_procedures': pending_procedures[:5],
    })


# ── Anesthesia Record List ────────────────────────────────────────────────────

@hms_permission_required('core.read_anesthesia_record')
def anesthesia_record_list(request):
    today = timezone.localdate()
    first_of_month = today.replace(day=1)

    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')

    from_date = first_of_month
    to_date = today

    if from_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    if to_date_str:
        try:
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    qs = (
        AnesthesiaRecord.objects
        .filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
        .select_related('visit__patient', 'visit__department', 'procedure_order', 'anesthesiologist')
        .order_by('-created_at')
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'anesthesia/record_list.html', {
        'page_obj': page_obj,
        'from_date': from_date,
        'to_date': to_date,
        'from_date_str': from_date.strftime('%Y-%m-%d'),
        'to_date_str': to_date.strftime('%Y-%m-%d'),
        'total_count': qs.count(),
    })


# ── Anesthesia Record Create ──────────────────────────────────────────────────

@hms_permission_required('core.write_anesthesia_record')
def anesthesia_record_create(request, visit_id):
    visit = get_object_or_404(
        Visit.objects.select_related('patient', 'department', 'doctor'),
        pk=visit_id,
    )
    procedure_orders = ProcedureOrder.objects.filter(
        visit=visit,
        status__in=['Scheduled', 'In Progress', 'Completed'],
    ).order_by('-ordered_at')

    anesthesia_types = AnesthesiaRecord.AnesthesiaType.choices
    asa_choices = AnesthesiaRecord.ASA.choices

    if request.method == 'POST':
        anesthesia_type = request.POST.get('anesthesia_type', '').strip()
        asa_classification = request.POST.get('asa_classification', '').strip()
        pre_op_assessment = request.POST.get('pre_op_assessment', '').strip()
        intra_op_notes = request.POST.get('intra_op_notes', '').strip()
        post_op_notes = request.POST.get('post_op_notes', '').strip()
        complications = request.POST.get('complications', '').strip()
        duration_str = request.POST.get('duration_minutes', '').strip()
        procedure_order_id = request.POST.get('procedure_order', '').strip()

        # Validate anesthesia type
        valid_types = [t for t, _ in anesthesia_types]
        if anesthesia_type not in valid_types:
            messages.error(request, 'Please select a valid anesthesia type.')
            return render(request, 'anesthesia/record_form.html', {
                'visit': visit,
                'procedure_orders': procedure_orders,
                'anesthesia_types': anesthesia_types,
                'asa_choices': asa_choices,
                'post_data': request.POST,
            })

        duration_minutes = None
        if duration_str:
            try:
                duration_minutes = int(duration_str)
                if duration_minutes < 0:
                    duration_minutes = None
            except ValueError:
                duration_minutes = None

        procedure_order = None
        if procedure_order_id:
            try:
                procedure_order = ProcedureOrder.objects.get(pk=procedure_order_id, visit=visit)
            except ProcedureOrder.DoesNotExist:
                pass

        record = AnesthesiaRecord.objects.create(
            visit=visit,
            procedure_order=procedure_order,
            anesthesiologist=request.user,
            anesthesia_type=anesthesia_type,
            asa_classification=asa_classification,
            pre_op_assessment=pre_op_assessment,
            intra_op_notes=intra_op_notes,
            post_op_notes=post_op_notes,
            complications=complications,
            duration_minutes=duration_minutes,
        )
        messages.success(request, f'Anesthesia record created for {visit.patient.full_name}.')
        return redirect('anesthesia_record_detail', record_id=record.pk)

    return render(request, 'anesthesia/record_form.html', {
        'visit': visit,
        'procedure_orders': procedure_orders,
        'anesthesia_types': anesthesia_types,
        'asa_choices': asa_choices,
        'is_create': True,
    })


# ── Anesthesia Record Detail ──────────────────────────────────────────────────

@hms_permission_required('core.read_anesthesia_record')
def anesthesia_record_detail(request, record_id):
    record = get_object_or_404(
        AnesthesiaRecord.objects.select_related(
            'visit__patient', 'visit__department', 'visit__doctor',
            'procedure_order', 'anesthesiologist',
        ),
        pk=record_id,
    )
    can_edit = request.user.has_perm('core.write_anesthesia_record')
    return render(request, 'anesthesia/record_detail.html', {
        'record': record,
        'can_edit': can_edit,
    })


# ── Anesthesia Record Edit ────────────────────────────────────────────────────

@hms_permission_required('core.write_anesthesia_record')
def anesthesia_record_edit(request, record_id):
    record = get_object_or_404(
        AnesthesiaRecord.objects.select_related(
            'visit__patient', 'visit__department', 'visit__doctor', 'procedure_order',
        ),
        pk=record_id,
    )
    visit = record.visit
    procedure_orders = ProcedureOrder.objects.filter(
        visit=visit,
        status__in=['Scheduled', 'In Progress', 'Completed'],
    ).order_by('-ordered_at')

    anesthesia_types = AnesthesiaRecord.AnesthesiaType.choices
    asa_choices = AnesthesiaRecord.ASA.choices

    if request.method == 'POST':
        anesthesia_type = request.POST.get('anesthesia_type', '').strip()
        asa_classification = request.POST.get('asa_classification', '').strip()
        pre_op_assessment = request.POST.get('pre_op_assessment', '').strip()
        intra_op_notes = request.POST.get('intra_op_notes', '').strip()
        post_op_notes = request.POST.get('post_op_notes', '').strip()
        complications = request.POST.get('complications', '').strip()
        duration_str = request.POST.get('duration_minutes', '').strip()
        procedure_order_id = request.POST.get('procedure_order', '').strip()

        valid_types = [t for t, _ in anesthesia_types]
        if anesthesia_type not in valid_types:
            messages.error(request, 'Please select a valid anesthesia type.')
            return render(request, 'anesthesia/record_form.html', {
                'record': record,
                'visit': visit,
                'procedure_orders': procedure_orders,
                'anesthesia_types': anesthesia_types,
                'asa_choices': asa_choices,
                'post_data': request.POST,
                'is_create': False,
            })

        duration_minutes = None
        if duration_str:
            try:
                duration_minutes = int(duration_str)
                if duration_minutes < 0:
                    duration_minutes = None
            except ValueError:
                duration_minutes = None

        procedure_order = None
        if procedure_order_id:
            try:
                procedure_order = ProcedureOrder.objects.get(pk=procedure_order_id, visit=visit)
            except ProcedureOrder.DoesNotExist:
                pass

        record.anesthesia_type = anesthesia_type
        record.asa_classification = asa_classification
        record.pre_op_assessment = pre_op_assessment
        record.intra_op_notes = intra_op_notes
        record.post_op_notes = post_op_notes
        record.complications = complications
        record.duration_minutes = duration_minutes
        record.procedure_order = procedure_order
        record.save()

        messages.success(request, 'Anesthesia record updated.')
        return redirect('anesthesia_record_detail', record_id=record.pk)

    return render(request, 'anesthesia/record_form.html', {
        'record': record,
        'visit': visit,
        'procedure_orders': procedure_orders,
        'anesthesia_types': anesthesia_types,
        'asa_choices': asa_choices,
        'is_create': False,
    })


# ── URL patterns to add to core/urls.py ──────────────────────────────────────
# from . import views_anesthesia
#
# path('anesthesia/', views_anesthesia.anesthesia_dashboard, name='anesthesia_dashboard'),
# path('anesthesia/records/', views_anesthesia.anesthesia_record_list, name='anesthesia_record_list'),
# path('anesthesia/visit/<int:visit_id>/new/', views_anesthesia.anesthesia_record_create, name='anesthesia_record_create'),
# path('anesthesia/record/<int:record_id>/', views_anesthesia.anesthesia_record_detail, name='anesthesia_record_detail'),
# path('anesthesia/record/<int:record_id>/edit/', views_anesthesia.anesthesia_record_edit, name='anesthesia_record_edit'),
