"""Employee Signature Management.

Upload/replace/remove handwritten, digital, or (future) electronic-approval
signatures for an Employee, and serve the active one back to any logged-in
user through an authenticated streaming view — MEDIA_URL is never wired to
Django's static file server in this project (see hospital_system/urls.py),
so this view is the only way a signature image is ever reachable, keeping
signature files off any publicly guessable URL.
"""
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .audit import log_action
from .models import AuditLog, Employee, EmployeeSignature


def _log(user, action, desc):
    log_action(user, action, AuditLog.Module.HR, description=desc)


def _can_manage(request_user, employee):
    """Administrators / HR Manager (core.manage_employee_signatures) can manage
    any employee's signature; every employee can also manage their own —
    matching the spec's "should not modify another employee's signature
    unless they have the required permission"."""
    return request_user.has_perm('core.manage_employee_signatures') or request_user.id == employee.user_id


@login_required
def employee_signature_upload(request, employee_id):
    employee = get_object_or_404(Employee.objects.select_related('user'), pk=employee_id)
    if not _can_manage(request.user, employee):
        messages.error(request, 'You are not authorized to manage this employee\'s signature.')
        return redirect('employee_detail', employee_id=employee.id)

    if request.method != 'POST':
        return redirect('employee_detail', employee_id=employee.id)

    file = request.FILES.get('file')
    signature_type = request.POST.get('signature_type', EmployeeSignature.SignatureType.HANDWRITTEN)
    reason = request.POST.get('reason', '').strip()

    if not file:
        messages.error(request, 'Please choose a signature file to upload.')
        return redirect('employee_detail', employee_id=employee.id)

    new_sig = EmployeeSignature(
        employee=employee, signature_type=signature_type, file=file,
        uploaded_by=request.user, reason=reason,
    )
    try:
        new_sig.full_clean()
    except ValidationError as e:
        for err in e.messages:
            messages.error(request, err)
        return redirect('employee_detail', employee_id=employee.id)

    with transaction.atomic():
        replaced = EmployeeSignature.objects.filter(employee=employee, is_active=True)
        was_replace = replaced.exists()
        replaced.update(is_active=False, deactivated_by=request.user, deactivated_at=timezone.now(), reason=reason)
        new_sig.save()

    _log(
        request.user, AuditLog.Action.UPDATE if was_replace else AuditLog.Action.CREATE,
        f'{"Replaced" if was_replace else "Uploaded"} signature for {employee.full_name} '
        f'({new_sig.get_signature_type_display()})' + (f' — reason: {reason}' if reason else ''),
    )
    messages.success(request, f'Signature {"replaced" if was_replace else "uploaded"} for {employee.full_name}.')
    return redirect('employee_detail', employee_id=employee.id)


@login_required
def employee_signature_remove(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)
    if not request.user.has_perm('core.delete_employee_signature'):
        messages.error(request, 'Only an administrator can remove an employee signature.')
        return redirect('employee_detail', employee_id=employee.id)

    if request.method != 'POST':
        return redirect('employee_detail', employee_id=employee.id)

    reason = request.POST.get('reason', '').strip()
    active = EmployeeSignature.objects.filter(employee=employee, is_active=True)
    removed_count = active.update(
        is_active=False, deactivated_by=request.user, deactivated_at=timezone.now(), reason=reason,
    )

    if removed_count:
        _log(request.user, AuditLog.Action.DELETE,
             f'Removed signature for {employee.full_name}' + (f' — reason: {reason}' if reason else ''))
        messages.success(request, f'Signature removed for {employee.full_name}.')
    else:
        messages.info(request, f'{employee.full_name} has no active signature to remove.')
    return redirect('employee_detail', employee_id=employee.id)


@login_required
def employee_signature_image(request, employee_id):
    """Stream the employee's current active signature file. Any authenticated
    user may fetch it — signatures are inserted automatically into shared
    clinical/administrative documents viewed across roles (e.g. a pharmacist
    viewing a doctor's signature on a prescription), so gating this further
    than "must be logged in" would break the very feature it supports."""
    employee = get_object_or_404(Employee, pk=employee_id)
    signature = EmployeeSignature.objects.filter(employee=employee, is_active=True).first()
    if not signature or not signature.file:
        raise Http404('No active signature for this employee.')
    content_type = mimetypes.guess_type(signature.file.name)[0] or 'application/octet-stream'
    return FileResponse(signature.file.open('rb'), content_type=content_type)
