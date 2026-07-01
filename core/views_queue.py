from django.contrib import messages
from django.db.models import Avg, F, Q
from django.forms import ModelForm
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import hms_permission_required
from .models import Queue, TriageAssessment, Visit


# ── Form ──────────────────────────────────────────────────────────────────────

class TriageAssessmentForm(ModelForm):
    class Meta:
        model = TriageAssessment
        fields = [
            'severity', 'chief_complaint',
            'temperature', 'bp_systolic', 'bp_diastolic',
            'pulse', 'respiratory_rate', 'spo2', 'weight',
            'notes',
        ]


# ── Queue Dashboard ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_queue')
def queue_dashboard(request):
    today = timezone.localdate()
    today_queues = (
        Queue.objects
        .filter(created_at__date=today)
        .select_related(
            'visit__patient',
            'visit__department',
            'visit__doctor',
        )
        .prefetch_related('visit__triage')
        .order_by('queue_number')
    )

    total_today = today_queues.count()
    waiting_count = today_queues.filter(status=Queue.Status.WAITING).count()
    called_count = today_queues.filter(status=Queue.Status.CALLED).count()
    in_progress_count = today_queues.filter(status=Queue.Status.IN_PROGRESS).count()
    completed_count = today_queues.filter(status=Queue.Status.COMPLETED).count()
    no_show_count = today_queues.filter(status=Queue.Status.NO_SHOW).count()

    # Avg wait time in minutes (from created_at to called_at for Called/InProgress/Completed)
    avg_wait_qs = today_queues.filter(
        called_at__isnull=False
    ).exclude(status=Queue.Status.NO_SHOW)

    avg_wait_minutes = None
    if avg_wait_qs.exists():
        total_minutes = 0
        count = 0
        for q in avg_wait_qs:
            delta = q.called_at - q.created_at
            total_minutes += delta.total_seconds() / 60
            count += 1
        if count:
            avg_wait_minutes = round(total_minutes / count)

    # Build queue list with extra attributes for template
    queue_entries = list(today_queues)
    now = timezone.now()
    for entry in queue_entries:
        # Minutes waiting (for Waiting/Called/In Progress)
        if entry.status in (Queue.Status.WAITING, Queue.Status.CALLED, Queue.Status.IN_PROGRESS):
            delta = now - entry.created_at
            entry.waited_minutes = int(delta.total_seconds() / 60)
        elif entry.completed_at:
            delta = entry.completed_at - entry.created_at
            entry.waited_minutes = int(delta.total_seconds() / 60)
        else:
            entry.waited_minutes = None

        # Triage info
        try:
            entry.triage_obj = entry.visit.triage
        except TriageAssessment.DoesNotExist:
            entry.triage_obj = None

    context = {
        'today': today,
        'queue_entries': queue_entries,
        'total_today': total_today,
        'waiting_count': waiting_count,
        'called_count': called_count,
        'in_progress_count': in_progress_count,
        'completed_count': completed_count,
        'no_show_count': no_show_count,
        'avg_wait_minutes': avg_wait_minutes,
        'queue_statuses': Queue.Status.choices,
    }
    return render(request, 'queue/dashboard.html', context)


# ── Call Next Patient ─────────────────────────────────────────────────────────

@hms_permission_required('core.manage_queue')
@require_POST
def queue_call_next(request):
    today = timezone.localdate()
    next_entry = (
        Queue.objects
        .filter(created_at__date=today, status=Queue.Status.WAITING)
        .order_by('queue_number')
        .first()
    )
    if next_entry:
        next_entry.status = Queue.Status.CALLED
        next_entry.called_at = timezone.now()
        next_entry.save()
        messages.success(
            request,
            f'Queue #{next_entry.queue_number} — {next_entry.visit.patient.full_name} has been called.'
        )
    else:
        messages.info(request, 'No patients are currently waiting in the queue.')
    return redirect('queue_dashboard')


# ── Update Queue Status ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_queue')
@require_POST
def queue_update_status(request, queue_id):
    entry = get_object_or_404(Queue, pk=queue_id)
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s[0] for s in Queue.Status.choices]

    if new_status in valid_statuses:
        old_status = entry.status
        entry.status = new_status

        # Set timestamps based on transition
        if new_status == Queue.Status.CALLED and not entry.called_at:
            entry.called_at = timezone.now()
        if new_status == Queue.Status.COMPLETED and not entry.completed_at:
            entry.completed_at = timezone.now()

        entry.save()
        messages.success(
            request,
            f'Queue #{entry.queue_number} status updated to "{new_status}".'
        )
    else:
        messages.error(request, 'Invalid status value.')

    return redirect('queue_dashboard')


# ── Triage Dashboard ──────────────────────────────────────────────────────────

@hms_permission_required('core.perform_triage')
def triage_dashboard(request):
    today = timezone.localdate()

    # Today's visits that do NOT yet have a triage assessment
    untriaged_visits = (
        Visit.objects
        .filter(created_at__date=today)
        .exclude(triage__isnull=False)
        .select_related('patient', 'department', 'doctor')
        .prefetch_related('queue')
        .order_by('created_at')
    )

    # Today's visits that DO have a triage assessment
    triaged_today = (
        TriageAssessment.objects
        .filter(created_at__date=today)
        .select_related('visit__patient', 'visit__department', 'triaged_by')
        .order_by('-created_at')
    )

    context = {
        'today': today,
        'untriaged_visits': untriaged_visits,
        'triaged_today': triaged_today,
    }
    return render(request, 'queue/triage_dashboard.html', context)


# ── Triage Create ─────────────────────────────────────────────────────────────

@hms_permission_required('core.perform_triage')
def triage_create(request, visit_id):
    visit = get_object_or_404(
        Visit.objects.select_related('patient', 'department', 'doctor'),
        pk=visit_id,
    )

    # Guard: already triaged
    if hasattr(visit, 'triage'):
        messages.info(request, 'This visit has already been triaged.')
        return redirect('triage_dashboard')

    if request.method == 'POST':
        form = TriageAssessmentForm(request.POST)
        if form.is_valid():
            assessment = form.save(commit=False)
            assessment.visit = visit
            assessment.triaged_by = request.user
            assessment.save()
            messages.success(
                request,
                f'Triage assessment saved for {visit.patient.full_name}.'
            )
            return redirect('triage_dashboard')
    else:
        form = TriageAssessmentForm()

    context = {
        'form': form,
        'visit': visit,
        'severity_choices': TriageAssessment.Severity.choices,
    }
    return render(request, 'queue/triage_form.html', context)


# URL_PATTERNS_TO_ADD = [
#     path('queue/', views_queue.queue_dashboard, name='queue_dashboard'),
#     path('queue/call-next/', views_queue.queue_call_next, name='queue_call_next'),
#     path('queue/<int:queue_id>/update-status/', views_queue.queue_update_status, name='queue_update_status'),
#     path('triage/', views_queue.triage_dashboard, name='triage_dashboard'),
#     path('triage/<int:visit_id>/assess/', views_queue.triage_create, name='triage_create'),
# ]
