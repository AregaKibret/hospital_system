"""
Patient Attachment & Document Management — upload (file/drag-drop/webcam
capture), categorize, link to clinical events, preview/download, replace
(with version history), soft-delete, comment, and report on patient
documents.

MEDIA_URL is never wired to Django's static file server in this project
(see core/views_signatures.py's docstring for the established precedent) —
every file is only ever reachable through the authenticated preview/download
views below, following employee_signature_image's exact FileResponse
pattern.
"""
import mimetypes

from django.contrib import messages
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    Admission, AttachmentCategory, AttachmentComment, AuditLog, ImagingOrder,
    LabOrder, Patient, PatientAttachment, ProcedureOrder, SurgeryOrder, Visit,
)

LINKABLE_FIELDS = {
    'visit': Visit, 'admission': Admission, 'surgery': SurgeryOrder,
    'lab_order': LabOrder, 'imaging_order': ImagingOrder, 'procedure_order': ProcedureOrder,
}


def _resolve_links(source):
    """Pull optional visit/admission/surgery/lab_order/imaging_order/
    procedure_order ids out of a GET or POST QueryDict and resolve them to
    real objects — used both to pre-fill the upload form and to persist the
    links on save."""
    resolved = {}
    for field, model in LINKABLE_FIELDS.items():
        raw = source.get(field)
        if raw:
            obj = model.objects.filter(pk=raw).first()
            if obj:
                resolved[field] = obj
    return resolved


def _uploader_department(user):
    employee = getattr(user, 'employee_profile', None)
    return employee.department if employee else None


def _visible_categories(user):
    """Categories any uploader may use, plus any category specifically
    restricted to a group the user belongs to."""
    return AttachmentCategory.objects.filter(is_active=True).filter(
        Q(restricted_to_roles__isnull=True) | Q(restricted_to_roles__in=user.groups.all())
    ).distinct().order_by('display_order', 'name')


def _visible_attachments(user, qs):
    if not user.has_perm('core.view_confidential_attachments'):
        qs = qs.exclude(is_confidential=True)
    return qs


def _log(user, action, desc, request=None):
    log_action(user, action, AuditLog.Module.DOCUMENT, description=desc, request=request)


# ── List / Timeline ──────────────────────────────────────────────────────────

@hms_permission_required('core.view_attachments')
def patient_attachment_list(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    qs = PatientAttachment.objects.select_related(
        'category', 'uploaded_by', 'department', 'visit', 'admission',
    ).prefetch_related('comments__author').filter(patient=patient, is_current=True, is_deleted=False)
    qs = _visible_attachments(request.user, qs)

    category_f = request.GET.get('category', '')
    dept_f = request.GET.get('department', '')
    uploader_f = request.GET.get('uploaded_by', '')
    visit_f = request.GET.get('visit', '')
    admission_f = request.GET.get('admission', '')
    file_type_f = request.GET.get('file_type', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if category_f:
        qs = qs.filter(category_id=category_f)
    if dept_f:
        qs = qs.filter(department_id=dept_f)
    if uploader_f:
        qs = qs.filter(uploaded_by_id=uploader_f)
    if visit_f:
        qs = qs.filter(visit_id=visit_f)
    if admission_f:
        qs = qs.filter(admission_id=admission_f)
    if file_type_f:
        qs = qs.filter(file_type=file_type_f)
    if date_from:
        qs = qs.filter(uploaded_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(uploaded_at__date__lte=date_to)

    qs = qs.order_by('-uploaded_at')
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'attachments/list.html', {
        'patient': patient,
        'page_obj': page_obj,
        'categories': AttachmentCategory.objects.filter(is_active=True),
        'category_f': category_f, 'dept_f': dept_f, 'uploader_f': uploader_f,
        'visit_f': visit_f, 'admission_f': admission_f, 'file_type_f': file_type_f,
        'date_from': date_from, 'date_to': date_to,
    })


# ── Upload ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.upload_attachment')
def patient_attachment_upload(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    prefill_links = _resolve_links(request.GET)
    allowed_categories = _visible_categories(request.user)

    if request.method == 'POST':
        file = request.FILES.get('file')
        title = request.POST.get('title', '').strip()
        category_id = request.POST.get('category')
        category = allowed_categories.filter(pk=category_id).first()

        if not file:
            messages.error(request, 'Please choose a file, or capture a photo, to upload.')
        elif not category:
            messages.error(request, 'Please choose a valid category — you may not be authorized for the one selected.')
        else:
            attachment = PatientAttachment(
                patient=patient,
                title=title or file.name,
                description=request.POST.get('description', '').strip(),
                category=category,
                file=file,
                uploaded_by=request.user,
                department=_uploader_department(request.user),
                is_confidential=bool(request.POST.get('is_confidential')),
                **{**_resolve_links(request.GET), **_resolve_links(request.POST)},
            )
            try:
                attachment.full_clean()
            except ValidationError as e:
                for err in e.messages:
                    messages.error(request, err)
                return render(request, 'attachments/upload_form.html', {
                    'patient': patient, 'categories': allowed_categories, 'links': prefill_links,
                })
            attachment.save()
            _log(
                request.user, AuditLog.Action.CREATE,
                f'Document uploaded for {patient.full_name}: "{attachment.title}" ({category.name})',
                request=request,
            )
            messages.success(request, f'"{attachment.title}" uploaded.')
            return redirect('patient_attachment_list', patient_id=patient.pk)

    return render(request, 'attachments/upload_form.html', {
        'patient': patient, 'categories': allowed_categories, 'links': prefill_links,
    })


# ── Replace (version history) ────────────────────────────────────────────────

@hms_permission_required('core.replace_attachment')
def patient_attachment_replace(request, attachment_id):
    old = get_object_or_404(PatientAttachment.objects.select_related('patient', 'category'), pk=attachment_id, is_current=True)
    allowed_categories = _visible_categories(request.user)

    if request.method == 'POST':
        file = request.FILES.get('file')
        if not file:
            messages.error(request, 'Please choose a replacement file.')
            return redirect('patient_attachment_list', patient_id=old.patient_id)

        new_version = PatientAttachment(
            patient=old.patient,
            title=request.POST.get('title', old.title).strip() or old.title,
            description=request.POST.get('description', old.description),
            category_id=request.POST.get('category') or old.category_id,
            file=file,
            uploaded_by=request.user,
            department=_uploader_department(request.user) or old.department,
            is_confidential=old.is_confidential,
            visit=old.visit, admission=old.admission, surgery=old.surgery,
            lab_order=old.lab_order, imaging_order=old.imaging_order, procedure_order=old.procedure_order,
            version=old.version + 1,
            previous_version=old,
        )
        try:
            new_version.full_clean()
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect('patient_attachment_list', patient_id=old.patient_id)

        with transaction.atomic():
            new_version.save()
            old.is_current = False
            old.save(update_fields=['is_current'])

        _log(
            request.user, AuditLog.Action.UPDATE,
            f'Document replaced for {old.patient.full_name}: "{old.title}" → v{new_version.version}',
            request=request,
        )
        messages.success(request, f'"{old.title}" replaced (now version {new_version.version}).')
        return redirect('patient_attachment_list', patient_id=old.patient_id)

    return render(request, 'attachments/replace_form.html', {
        'old': old, 'categories': allowed_categories,
    })


@hms_permission_required('core.view_attachments')
def attachment_version_history(request, attachment_id):
    attachment = get_object_or_404(PatientAttachment.objects.select_related('patient', 'uploaded_by'), pk=attachment_id)
    chain = [attachment]

    node = attachment
    while node.previous_version_id:
        node = node.previous_version
        chain.append(node)

    node = attachment
    while True:
        newer = PatientAttachment.objects.filter(previous_version=node).first()
        if not newer:
            break
        chain.append(newer)
        node = newer

    chain.sort(key=lambda a: -a.version)
    return render(request, 'attachments/version_history.html', {
        'attachment': attachment, 'chain': chain,
    })


# ── Preview / Download (authenticated file serving) ──────────────────────────

def _serve(request, attachment_id, disposition):
    attachment = get_object_or_404(
        PatientAttachment.objects.select_related('patient'), pk=attachment_id, is_deleted=False,
    )
    if attachment.is_confidential and not request.user.has_perm('core.view_confidential_attachments'):
        raise Http404('Document not found.')
    if not attachment.file:
        raise Http404('File missing.')

    if attachment.is_confidential:
        _log(
            request.user, AuditLog.Action.ACCESS,
            f'Confidential document accessed: "{attachment.title}" ({attachment.patient.full_name})',
            request=request,
        )
    content_type = mimetypes.guess_type(attachment.file.name)[0] or 'application/octet-stream'
    response = FileResponse(attachment.file.open('rb'), content_type=content_type)
    filename = attachment.file_name or attachment.file.name.rsplit('/', 1)[-1]
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response


@hms_permission_required('core.view_attachments')
def patient_attachment_preview(request, attachment_id):
    return _serve(request, attachment_id, 'inline')


@hms_permission_required('core.view_attachments')
def patient_attachment_download(request, attachment_id):
    return _serve(request, attachment_id, 'attachment')


# ── Delete / Restore ──────────────────────────────────────────────────────────

@require_POST
@hms_permission_required('core.delete_attachment')
def patient_attachment_delete(request, attachment_id):
    attachment = get_object_or_404(PatientAttachment.objects.select_related('patient'), pk=attachment_id)
    reason = request.POST.get('reason', '').strip()
    attachment.is_deleted = True
    attachment.deleted_by = request.user
    attachment.deleted_at = timezone.now()
    attachment.delete_reason = reason
    attachment.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at', 'delete_reason'])
    _log(
        request.user, AuditLog.Action.DELETE,
        f'Document deleted for {attachment.patient.full_name}: "{attachment.title}". Reason: {reason or "—"}',
        request=request,
    )
    messages.warning(request, f'"{attachment.title}" deleted.')
    return redirect('patient_attachment_list', patient_id=attachment.patient_id)


@require_POST
@hms_permission_required('core.restore_attachment')
def patient_attachment_restore(request, attachment_id):
    attachment = get_object_or_404(PatientAttachment.objects.select_related('patient'), pk=attachment_id, is_deleted=True)
    attachment.is_deleted = False
    attachment.deleted_by = None
    attachment.deleted_at = None
    attachment.delete_reason = ''
    attachment.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at', 'delete_reason'])
    _log(
        request.user, AuditLog.Action.UPDATE,
        f'Document restored for {attachment.patient.full_name}: "{attachment.title}"',
        request=request,
    )
    messages.success(request, f'"{attachment.title}" restored.')
    return redirect('patient_attachment_list', patient_id=attachment.patient_id)


@hms_permission_required('core.delete_attachment')
def patient_attachment_deleted_list(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    qs = PatientAttachment.objects.select_related('category', 'deleted_by').filter(
        patient=patient, is_deleted=True,
    ).order_by('-deleted_at')
    return render(request, 'attachments/deleted_list.html', {'patient': patient, 'attachments': qs})


# ── Comments ──────────────────────────────────────────────────────────────────

@require_POST
@hms_permission_required('core.view_attachments')
def attachment_comment_add(request, attachment_id):
    attachment = get_object_or_404(PatientAttachment, pk=attachment_id)
    text = request.POST.get('comment', '').strip()
    if text:
        AttachmentComment.objects.create(attachment=attachment, author=request.user, comment=text)
        _log(request.user, AuditLog.Action.CREATE, f'Comment added on "{attachment.title}"', request=request)
        messages.success(request, 'Comment added.')
    return redirect('patient_attachment_list', patient_id=attachment.patient_id)


# ── Attachment Category CRUD ─────────────────────────────────────────────────

@hms_permission_required('core.manage_attachment_categories')
def attachment_category_list(request):
    categories = AttachmentCategory.objects.all().prefetch_related('restricted_to_roles').order_by('display_order', 'name')
    return render(request, 'attachments/category_list.html', {'categories': categories})


@hms_permission_required('core.manage_attachment_categories')
def attachment_category_create(request):
    groups = Group.objects.order_by('name')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Category name is required.')
        else:
            cat = AttachmentCategory.objects.create(
                name=name, description=request.POST.get('description', '').strip(),
                display_order=int(request.POST.get('display_order', 0) or 0),
                is_required_for_admission=bool(request.POST.get('is_required_for_admission')),
            )
            cat.restricted_to_roles.set(request.POST.getlist('restricted_to_roles'))
            _log(request.user, AuditLog.Action.CREATE, f'Attachment category "{cat.name}" created', request=request)
            messages.success(request, f'Category "{cat.name}" created.')
            return redirect('attachment_category_list')
    return render(request, 'attachments/category_form.html', {'action': 'Create', 'groups': groups})


@hms_permission_required('core.manage_attachment_categories')
def attachment_category_edit(request, category_id):
    cat = get_object_or_404(AttachmentCategory, pk=category_id)
    groups = Group.objects.order_by('name')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Category name is required.')
        else:
            cat.name = name
            cat.description = request.POST.get('description', '').strip()
            cat.display_order = int(request.POST.get('display_order', cat.display_order) or 0)
            cat.is_required_for_admission = bool(request.POST.get('is_required_for_admission'))
            cat.is_active = bool(request.POST.get('is_active'))
            cat.save()
            cat.restricted_to_roles.set(request.POST.getlist('restricted_to_roles'))
            _log(request.user, AuditLog.Action.UPDATE, f'Attachment category "{cat.name}" updated', request=request)
            messages.success(request, f'Category "{cat.name}" updated.')
            return redirect('attachment_category_list')
    return render(request, 'attachments/category_form.html', {'action': 'Edit', 'category': cat, 'groups': groups})
