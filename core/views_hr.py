from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import hms_permission_required
from .models import Attendance, Department, Employee, LeaveRequest

User = get_user_model()


# ── HR Dashboard ──────────────────────────────────────────────────────────────

@hms_permission_required('core.read_employee')
def hr_dashboard(request):
    today = timezone.localdate()
    first_of_month = today.replace(day=1)

    total_employees = Employee.objects.count()
    active_employees = Employee.objects.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count()
    on_leave = Employee.objects.filter(employment_status=Employee.EmploymentStatus.ON_LEAVE).count()
    new_this_month = Employee.objects.filter(hire_date__gte=first_of_month).count()
    pending_leaves = LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).count()

    dept_breakdown = (
        Department.objects
        .annotate(emp_count=Count('employees'))
        .order_by('-emp_count')
    )

    # Today's attendance summary
    today_attendance = Attendance.objects.filter(date=today)
    att_present = today_attendance.filter(status=Attendance.AttendanceStatus.PRESENT).count()
    att_late = today_attendance.filter(status=Attendance.AttendanceStatus.LATE).count()
    att_absent = today_attendance.filter(status=Attendance.AttendanceStatus.ABSENT).count()
    att_on_leave = today_attendance.filter(status=Attendance.AttendanceStatus.ON_LEAVE).count()
    att_marked = today_attendance.count()

    context = {
        'total_employees': total_employees,
        'active_employees': active_employees,
        'on_leave': on_leave,
        'new_this_month': new_this_month,
        'pending_leaves': pending_leaves,
        'dept_breakdown': dept_breakdown,
        'att_present': att_present,
        'att_late': att_late,
        'att_absent': att_absent,
        'att_on_leave': att_on_leave,
        'att_marked': att_marked,
        'today': today,
    }
    return render(request, 'hr/dashboard.html', context)


# ── Employee List ─────────────────────────────────────────────────────────────

@hms_permission_required('core.read_employee')
def employee_list(request):
    query = request.GET.get('q', '').strip()
    dept_id = request.GET.get('department', '').strip()
    emp_type = request.GET.get('employment_type', '').strip()
    emp_status = request.GET.get('employment_status', '').strip()

    employees = Employee.objects.select_related('user', 'department').order_by('user__last_name', 'user__first_name')

    if query:
        employees = employees.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(position__icontains=query)
        )
    if dept_id:
        employees = employees.filter(department_id=dept_id)
    if emp_type:
        employees = employees.filter(employment_type=emp_type)
    if emp_status:
        employees = employees.filter(employment_status=emp_status)

    departments = Department.objects.all()
    paginator = Paginator(employees, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'hr/employee_list.html', {
        'page_obj': page_obj,
        'query': query,
        'dept_id': dept_id,
        'emp_type': emp_type,
        'emp_status': emp_status,
        'departments': departments,
        'employment_type_choices': Employee.EmploymentType.choices,
        'employment_status_choices': Employee.EmploymentStatus.choices,
    })


# ── Employee Create ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_employees')
def employee_create(request):
    departments = Department.objects.all()
    # Users without an existing employee profile
    existing_emp_user_ids = Employee.objects.values_list('user_id', flat=True)
    available_users = User.objects.exclude(id__in=existing_emp_user_ids).order_by('last_name', 'first_name')

    if request.method == 'POST':
        user_id = request.POST.get('user') or None
        if not user_id:
            messages.error(request, 'Please select a user.')
        else:
            user = get_object_or_404(User, pk=user_id)
            if Employee.objects.filter(user=user).exists():
                messages.error(request, f'Employee profile already exists for {user.get_full_name()}.')
            else:
                dept_id = request.POST.get('department') or None
                Employee.objects.create(
                    user=user,
                    department_id=dept_id,
                    position=request.POST.get('position', '').strip(),
                    employment_type=request.POST.get('employment_type', Employee.EmploymentType.PERMANENT),
                    employment_status=request.POST.get('employment_status', Employee.EmploymentStatus.ACTIVE),
                    hire_date=request.POST.get('hire_date') or None,
                    basic_salary=request.POST.get('basic_salary', 0) or 0,
                    national_id=request.POST.get('national_id', '').strip(),
                    emergency_contact_name=request.POST.get('emergency_contact_name', '').strip(),
                    emergency_contact_phone=request.POST.get('emergency_contact_phone', '').strip(),
                )
                messages.success(request, f'Employee profile created for {user.get_full_name() or user.username}.')
                return redirect('employee_list')

    return render(request, 'hr/employee_form.html', {
        'action': 'Create',
        'employee': None,
        'departments': departments,
        'available_users': available_users,
        'employment_type_choices': Employee.EmploymentType.choices,
        'employment_status_choices': Employee.EmploymentStatus.choices,
    })


# ── Employee Detail ───────────────────────────────────────────────────────────

@hms_permission_required('core.read_employee')
def employee_detail(request, employee_id):
    employee = get_object_or_404(
        Employee.objects.select_related('user', 'department'),
        pk=employee_id,
    )
    today = timezone.localdate()
    first_of_month = today.replace(day=1)

    month_attendance = Attendance.objects.filter(
        employee=employee,
        date__gte=first_of_month,
        date__lte=today,
    )
    att_present = month_attendance.filter(status=Attendance.AttendanceStatus.PRESENT).count()
    att_late = month_attendance.filter(status=Attendance.AttendanceStatus.LATE).count()
    att_absent = month_attendance.filter(status=Attendance.AttendanceStatus.ABSENT).count()
    att_half_day = month_attendance.filter(status=Attendance.AttendanceStatus.HALF_DAY).count()

    leave_history = (
        LeaveRequest.objects
        .filter(employee=employee)
        .select_related('reviewed_by')
        .order_by('-requested_at')[:10]
    )

    return render(request, 'hr/employee_detail.html', {
        'employee': employee,
        'att_present': att_present,
        'att_late': att_late,
        'att_absent': att_absent,
        'att_half_day': att_half_day,
        'leave_history': leave_history,
        'today': today,
    })


# ── Employee Edit ─────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_employees')
def employee_edit(request, employee_id):
    employee = get_object_or_404(Employee.objects.select_related('user'), pk=employee_id)
    departments = Department.objects.all()

    if request.method == 'POST':
        dept_id = request.POST.get('department') or None
        employee.department_id = dept_id
        employee.position = request.POST.get('position', '').strip()
        employee.employment_type = request.POST.get('employment_type', employee.employment_type)
        employee.employment_status = request.POST.get('employment_status', employee.employment_status)
        employee.hire_date = request.POST.get('hire_date') or None
        employee.basic_salary = request.POST.get('basic_salary', 0) or 0
        employee.national_id = request.POST.get('national_id', '').strip()
        employee.emergency_contact_name = request.POST.get('emergency_contact_name', '').strip()
        employee.emergency_contact_phone = request.POST.get('emergency_contact_phone', '').strip()
        employee.save()
        messages.success(request, 'Employee profile updated.')
        return redirect('employee_detail', employee_id=employee_id)

    return render(request, 'hr/employee_form.html', {
        'action': 'Edit',
        'employee': employee,
        'departments': departments,
        'available_users': None,
        'employment_type_choices': Employee.EmploymentType.choices,
        'employment_status_choices': Employee.EmploymentStatus.choices,
    })


# ── Attendance List ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_attendance')
def attendance_list(request):
    date_str = request.GET.get('date', '').strip()
    dept_id = request.GET.get('department', '').strip()

    if date_str:
        try:
            from datetime import date
            filter_date = date.fromisoformat(date_str)
        except ValueError:
            filter_date = timezone.localdate()
    else:
        filter_date = timezone.localdate()

    employees_qs = Employee.objects.select_related('user', 'department').filter(
        employment_status=Employee.EmploymentStatus.ACTIVE
    ).order_by('user__last_name', 'user__first_name')

    if dept_id:
        employees_qs = employees_qs.filter(department_id=dept_id)

    # Build attendance map for the date
    att_records = {
        a.employee_id: a
        for a in Attendance.objects.filter(date=filter_date).select_related('employee')
    }

    employee_attendance = []
    for emp in employees_qs:
        employee_attendance.append({
            'employee': emp,
            'record': att_records.get(emp.pk),
        })

    departments = Department.objects.all()
    return render(request, 'hr/attendance_list.html', {
        'employee_attendance': employee_attendance,
        'filter_date': filter_date,
        'dept_id': dept_id,
        'departments': departments,
        'status_choices': Attendance.AttendanceStatus.choices,
    })


# ── Attendance Mark ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_attendance')
@require_POST
def attendance_mark(request):
    employee_id = request.POST.get('employee_id')
    status = request.POST.get('status', Attendance.AttendanceStatus.PRESENT)
    time_in = request.POST.get('time_in') or None
    time_out = request.POST.get('time_out') or None
    notes = request.POST.get('notes', '').strip()
    date_str = request.POST.get('date', '').strip()

    if date_str:
        try:
            from datetime import date
            mark_date = date.fromisoformat(date_str)
        except ValueError:
            mark_date = timezone.localdate()
    else:
        mark_date = timezone.localdate()

    employee = get_object_or_404(Employee, pk=employee_id)
    record, created = Attendance.objects.update_or_create(
        employee=employee,
        date=mark_date,
        defaults={
            'status': status,
            'time_in': time_in,
            'time_out': time_out,
            'notes': notes,
            'recorded_by': request.user,
        },
    )
    action = 'Marked' if created else 'Updated'
    messages.success(request, f'{action} attendance for {employee.full_name} — {status}.')
    return redirect(f"{request.POST.get('next', '')}?date={mark_date}" if request.POST.get('next') else f"{'/attendance/'}?date={mark_date}")


# ── Leave Request List ────────────────────────────────────────────────────────

@hms_permission_required('core.read_employee')
def leave_request_list(request):
    status_filter = request.GET.get('status', '').strip()
    leaves = (
        LeaveRequest.objects
        .select_related('employee__user', 'employee__department', 'reviewed_by')
        .order_by('-requested_at')
    )
    if status_filter:
        leaves = leaves.filter(status=status_filter)

    paginator = Paginator(leaves, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'hr/leave_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'status_choices': LeaveRequest.Status.choices,
    })


# ── Leave Request Create ──────────────────────────────────────────────────────

@hms_permission_required('core.read_employee')
def leave_request_create(request):
    # Try to find the employee profile of the current user
    try:
        employee = request.user.employee_profile
    except Employee.DoesNotExist:
        employee = None

    if request.method == 'POST':
        if not employee:
            messages.error(request, 'You do not have an employee profile. Contact HR.')
            return redirect('leave_request_list')

        leave_type = request.POST.get('leave_type', '').strip()
        start_date = request.POST.get('start_date', '').strip()
        end_date = request.POST.get('end_date', '').strip()
        reason = request.POST.get('reason', '').strip()

        if not leave_type or not start_date or not end_date:
            messages.error(request, 'Leave type, start date, and end date are required.')
        else:
            from datetime import date
            try:
                sd = date.fromisoformat(start_date)
                ed = date.fromisoformat(end_date)
                if ed < sd:
                    messages.error(request, 'End date cannot be before start date.')
                else:
                    days = (ed - sd).days + 1
                    days_requested = int(request.POST.get('days_requested', days) or days)
                    LeaveRequest.objects.create(
                        employee=employee,
                        leave_type=leave_type,
                        start_date=sd,
                        end_date=ed,
                        days_requested=days_requested,
                        reason=reason,
                    )
                    messages.success(request, 'Leave request submitted successfully.')
                    return redirect('leave_request_list')
            except ValueError:
                messages.error(request, 'Invalid date format.')

    return render(request, 'hr/leave_form.html', {
        'employee': employee,
        'leave_type_choices': LeaveRequest.LeaveType.choices,
    })


# ── Leave Request Review ──────────────────────────────────────────────────────

@hms_permission_required('core.manage_employees')
@require_POST
def leave_request_review(request, request_id):
    leave = get_object_or_404(LeaveRequest, pk=request_id)
    if leave.status != LeaveRequest.Status.PENDING:
        messages.error(request, 'This leave request has already been reviewed.')
        return redirect('leave_request_list')

    action = request.POST.get('action', '').strip()
    review_notes = request.POST.get('review_notes', '').strip()

    if action == 'approve':
        leave.status = LeaveRequest.Status.APPROVED
        # Update employee status if currently active
        emp = leave.employee
        if emp.employment_status == Employee.EmploymentStatus.ACTIVE:
            emp.employment_status = Employee.EmploymentStatus.ON_LEAVE
            emp.save()
        msg = 'Leave request approved.'
    elif action == 'reject':
        leave.status = LeaveRequest.Status.REJECTED
        msg = 'Leave request rejected.'
    else:
        messages.error(request, 'Invalid action.')
        return redirect('leave_request_list')

    leave.reviewed_by = request.user
    leave.review_notes = review_notes
    leave.save()
    messages.success(request, msg)
    return redirect('leave_request_list')


# ── URL Reference ─────────────────────────────────────────────────────────────
# hr_dashboard               GET  /hr/
# employee_list              GET  /hr/employees/
# employee_create            GET/POST  /hr/employees/create/
# employee_detail            GET  /hr/employees/<employee_id>/
# employee_edit              GET/POST  /hr/employees/<employee_id>/edit/
# attendance_list            GET  /hr/attendance/
# attendance_mark            POST /hr/attendance/mark/
# leave_request_list         GET  /hr/leave/
# leave_request_create       GET/POST  /hr/leave/create/
# leave_request_review       POST /hr/leave/<request_id>/review/
