"""Physical Inventory Count (Stock Take) & Inventory Period Management.

Spans the three existing stock domains — General Store (InventoryItem),
Medication (MedicationBatch), and Department Store (DepartmentStock) —
without altering any of them. See models.py's PHYSICAL INVENTORY COUNT
section for the full design rationale.
"""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, DepartmentStock, DepartmentStore, InventoryAdjustment, InventoryCategory,
    InventoryItem, InventoryPeriod, InventoryPeriodBalance, InventoryTransaction,
    Medication, MedicationBatch, PhysicalCount, PhysicalCountLine, StockTransaction,
)


def _log(user, action, desc):
    log_action(user, action, AuditLog.Module.INVENTORY, description=desc)


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY PERIOD MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.manage_inventory_periods')
def inventory_period_list(request):
    periods = InventoryPeriod.objects.select_related('opened_by', 'closed_by', 'previous_period').order_by('-start_date')
    return render(request, 'physical_count/period_list.html', {'periods': periods})


@login_required
@hms_permission_required('core.manage_inventory_periods')
def inventory_period_create(request):
    closed_periods = InventoryPeriod.objects.filter(status=InventoryPeriod.Status.CLOSED).order_by('-end_date')

    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        start_date = p.get('start_date')
        end_date = p.get('end_date')
        previous_period_id = p.get('previous_period') or None

        errors = []
        if not name:
            errors.append('Period name is required.')
        if InventoryPeriod.objects.filter(name__iexact=name).exists():
            errors.append(f'A period named "{name}" already exists.')
        if not start_date or not end_date:
            errors.append('Start and end dates are required.')

        previous_period = None
        if previous_period_id:
            previous_period = get_object_or_404(InventoryPeriod, pk=previous_period_id, status=InventoryPeriod.Status.CLOSED)

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            with transaction.atomic():
                period = InventoryPeriod.objects.create(
                    name=name,
                    period_type=p.get('period_type', InventoryPeriod.PeriodType.ANNUAL),
                    start_date=start_date,
                    end_date=end_date,
                    previous_period=previous_period,
                    opened_by=request.user,
                    notes=p.get('notes', '').strip(),
                )
                carried = 0
                if previous_period:
                    carried = _carry_forward_balances(period, previous_period, request.user)

            _log(request.user, AuditLog.Action.CREATE,
                 f'Inventory period "{period.name}" opened'
                 + (f' — {carried} opening balance row(s) carried forward from "{previous_period.name}"' if previous_period else ''))
            messages.success(
                request,
                f'Inventory period "{period.name}" opened.'
                + (f' {carried} opening balance(s) carried forward from {previous_period.name}.' if previous_period else ''),
            )
            return redirect('inventory_period_detail', pk=period.pk)

    return render(request, 'physical_count/period_form.html', {
        'closed_periods': closed_periods,
        'period_types': InventoryPeriod.PeriodType.choices,
        'today': timezone.localdate(),
    })


def _carry_forward_balances(new_period, previous_period, user):
    """Copy every Closing balance row of `previous_period` into `new_period`
    as Opening balance rows — the literal, traceable "closing becomes
    opening" required by the spec."""
    closing_rows = InventoryPeriodBalance.objects.filter(
        period=previous_period, balance_type=InventoryPeriodBalance.BalanceType.CLOSING,
    )
    new_rows = [
        InventoryPeriodBalance(
            period=new_period,
            balance_type=InventoryPeriodBalance.BalanceType.OPENING,
            domain=row.domain,
            inventory_item=row.inventory_item,
            medication=row.medication,
            department_stock=row.department_stock,
            item_code=row.item_code,
            item_name=row.item_name,
            category_name=row.category_name,
            quantity=row.quantity,
            unit_cost=row.unit_cost,
            valuation=row.valuation,
            carried_from_period=row,
        )
        for row in closing_rows
    ]
    InventoryPeriodBalance.objects.bulk_create(new_rows)
    return len(new_rows)


@login_required
@hms_permission_required('core.manage_inventory_periods')
def inventory_period_detail(request, pk):
    period = get_object_or_404(InventoryPeriod.objects.select_related('opened_by', 'closed_by', 'previous_period'), pk=pk)
    counts = period.physical_counts.select_related('created_by', 'approved_by').order_by('-created_at')
    opening_balances = period.balances.filter(balance_type=InventoryPeriodBalance.BalanceType.OPENING)
    closing_balances = period.balances.filter(balance_type=InventoryPeriodBalance.BalanceType.CLOSING)
    opening_total = sum((b.valuation for b in opening_balances), Decimal('0.00'))
    closing_total = sum((b.valuation for b in closing_balances), Decimal('0.00'))
    return render(request, 'physical_count/period_detail.html', {
        'period': period,
        'counts': counts,
        'opening_count': opening_balances.count(),
        'closing_count': closing_balances.count(),
        'opening_total': opening_total,
        'closing_total': closing_total,
    })


@login_required
@hms_permission_required('core.manage_inventory_periods')
def inventory_period_close(request, pk):
    period = get_object_or_404(InventoryPeriod, pk=pk, status=InventoryPeriod.Status.OPEN)
    if request.method == 'POST':
        with transaction.atomic():
            snapshotted = _snapshot_closing_balances(period)
            period.status = InventoryPeriod.Status.CLOSED
            period.closed_by = request.user
            period.closed_at = timezone.now()
            period.save(update_fields=['status', 'closed_by', 'closed_at'])
        _log(request.user, AuditLog.Action.UPDATE,
             f'Inventory period "{period.name}" closed — {snapshotted} closing balance row(s) snapshotted')
        messages.success(request, f'Period "{period.name}" closed. {snapshotted} item balances snapshotted as the closing position.')
    return redirect('inventory_period_detail', pk=pk)


def _snapshot_closing_balances(period):
    """Snapshot the CURRENT system quantity of every active item across all
    three domains as this period's Closing balance. Because approving a
    physical count already syncs system quantity to the verified physical
    count, whatever is on hand at close time reflects the last approved
    reconciliation for anything that was counted this period."""
    rows = []
    for item in InventoryItem.objects.filter(is_active=True).select_related('category'):
        rows.append(InventoryPeriodBalance(
            period=period, balance_type=InventoryPeriodBalance.BalanceType.CLOSING,
            domain=PhysicalCount.Domain.GENERAL_STORE, inventory_item=item,
            item_code=item.sku, item_name=item.name,
            category_name=item.category.name if item.category else '',
            quantity=item.quantity_in_stock, unit_cost=item.unit_cost,
            valuation=item.quantity_in_stock * item.unit_cost,
        ))
    for med in Medication.objects.filter(inventory_item__is_active=True).select_related('inventory_item__category'):
        qty = med.current_stock
        rows.append(InventoryPeriodBalance(
            period=period, balance_type=InventoryPeriodBalance.BalanceType.CLOSING,
            domain=PhysicalCount.Domain.MEDICATION, medication=med,
            item_code=med.code, item_name=med.name,
            category_name=med.category.name if med.category else '',
            quantity=qty, unit_cost=med.purchase_price,
            valuation=qty * med.purchase_price,
        ))
    for ds in DepartmentStock.objects.select_related('medication', 'medication__inventory_item__category', 'department_store'):
        rows.append(InventoryPeriodBalance(
            period=period, balance_type=InventoryPeriodBalance.BalanceType.CLOSING,
            domain=PhysicalCount.Domain.DEPARTMENT_STORE, department_stock=ds,
            item_code=ds.medication.code, item_name=f'{ds.medication.name} — {ds.department_store.name}',
            category_name=ds.medication.category.name if ds.medication.category else '',
            quantity=ds.quantity_available, unit_cost=ds.medication.purchase_price,
            valuation=ds.quantity_available * ds.medication.purchase_price,
        ))
    InventoryPeriodBalance.objects.bulk_create(rows)
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL COUNT WORKFLOW
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.perform_stock_count')
def physical_count_list(request):
    counts = PhysicalCount.objects.select_related('created_by', 'approved_by', 'period', 'department_store').order_by('-created_at')
    status_f = request.GET.get('status', '')
    domain_f = request.GET.get('domain', '')
    if status_f:
        counts = counts.filter(status=status_f)
    if domain_f:
        counts = counts.filter(domain=domain_f)
    return render(request, 'physical_count/count_list.html', {
        'counts': counts,
        'status_f': status_f, 'domain_f': domain_f,
        'status_choices': PhysicalCount.Status.choices,
        'domain_choices': PhysicalCount.Domain.choices,
    })


@login_required
@hms_permission_required('core.perform_stock_count')
def physical_count_create(request):
    open_periods = InventoryPeriod.objects.filter(status=InventoryPeriod.Status.OPEN).order_by('-start_date')
    categories = InventoryCategory.objects.order_by('name')
    department_stores = DepartmentStore.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        p = request.POST
        domain = p.get('domain', PhysicalCount.Domain.GENERAL_STORE)
        errors = []

        department_store = None
        if domain == PhysicalCount.Domain.DEPARTMENT_STORE:
            dept_store_id = p.get('department_store') or None
            if not dept_store_id:
                errors.append('Select a department store for a Department Store count.')
            else:
                department_store = get_object_or_404(DepartmentStore, pk=dept_store_id)

        category = None
        if p.get('category'):
            category = get_object_or_404(InventoryCategory, pk=p['category'])

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            with transaction.atomic():
                count = PhysicalCount.objects.create(
                    period_id=p.get('period') or None,
                    count_type=p.get('count_type', PhysicalCount.CountType.ANNUAL),
                    domain=domain,
                    category=category,
                    department_store=department_store,
                    freeze_snapshot=bool(p.get('freeze_snapshot', 'on')),
                    status=PhysicalCount.Status.IN_PROGRESS,
                    notes=p.get('notes', '').strip(),
                    created_by=request.user,
                )
                line_count = _populate_count_lines(count)

            _log(request.user, AuditLog.Action.CREATE,
                 f'Physical inventory count {count.count_number} started ({count.get_domain_display()}, '
                 f'{count.get_count_type_display()}) — {line_count} item(s)')
            messages.success(request, f'Count {count.count_number} started with {line_count} item(s).')
            return redirect('physical_count_detail', pk=count.pk)

    return render(request, 'physical_count/count_create.html', {
        'open_periods': open_periods,
        'categories': categories,
        'department_stores': department_stores,
        'count_types': PhysicalCount.CountType.choices,
        'domain_choices': PhysicalCount.Domain.choices,
    })


def _populate_count_lines(count):
    """Auto-populate PhysicalCountLine rows from the appropriate domain,
    snapshotting system quantity + item details at this moment."""
    lines = []

    if count.domain == PhysicalCount.Domain.GENERAL_STORE:
        items_qs = InventoryItem.objects.filter(is_active=True).select_related('category')
        if count.category:
            items_qs = items_qs.filter(category=count.category)
        for item in items_qs:
            lines.append(PhysicalCountLine(
                physical_count=count, inventory_item=item,
                item_code=item.sku, item_name=item.name,
                category_name=item.category.name if item.category else '',
                unit_of_measure=item.unit,
                system_quantity=item.quantity_in_stock, unit_cost=item.unit_cost,
            ))

    elif count.domain == PhysicalCount.Domain.MEDICATION:
        batches_qs = MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0,
        ).select_related('medication', 'medication__inventory_item__category')
        if count.category:
            batches_qs = batches_qs.filter(medication__inventory_item__category=count.category)
        for batch in batches_qs:
            med = batch.medication
            lines.append(PhysicalCountLine(
                physical_count=count, medication_batch=batch,
                item_code=med.code, item_name=med.name,
                category_name=med.category.name if med.category else '',
                unit_of_measure=med.unit_of_measure,
                batch_number=batch.batch_number, expiration_date=batch.expiration_date,
                system_quantity=batch.quantity_available, unit_cost=batch.purchase_price,
            ))

    elif count.domain == PhysicalCount.Domain.DEPARTMENT_STORE:
        stock_qs = DepartmentStock.objects.filter(
            department_store=count.department_store,
        ).select_related('medication', 'medication__inventory_item__category')
        if count.category:
            stock_qs = stock_qs.filter(medication__inventory_item__category=count.category)
        for ds in stock_qs:
            med = ds.medication
            lines.append(PhysicalCountLine(
                physical_count=count, department_stock=ds,
                item_code=med.code, item_name=med.name,
                category_name=med.category.name if med.category else '',
                unit_of_measure=med.unit_of_measure,
                system_quantity=ds.quantity_available, unit_cost=med.purchase_price,
            ))

    PhysicalCountLine.objects.bulk_create(lines)
    return len(lines)


@login_required
@hms_permission_required('core.perform_stock_count')
def physical_count_detail(request, pk):
    count = get_object_or_404(
        PhysicalCount.objects.select_related('created_by', 'approved_by', 'period', 'department_store', 'category'),
        pk=pk,
    )
    lines = count.lines.select_related('counted_by', 'verified_by').order_by('item_name')
    return render(request, 'physical_count/count_detail.html', {
        'count': count,
        'lines': lines,
        'can_approve': request.user.has_perm('core.approve_stock_count'),
    })


@login_required
@hms_permission_required('core.perform_stock_count')
def physical_count_enter(request, pk):
    count = get_object_or_404(PhysicalCount, pk=pk)
    if count.status != PhysicalCount.Status.IN_PROGRESS:
        messages.error(request, 'This count is not in progress.')
        return redirect('physical_count_detail', pk=pk)

    if request.method == 'POST':
        for line in count.lines.all():
            val = request.POST.get(f'counted_{line.pk}', '')
            if val != '':
                try:
                    line.physical_quantity = Decimal(val)
                    line.remarks = request.POST.get(f'remarks_{line.pk}', '').strip()
                    line.counted_by = request.user
                    line.counted_at = timezone.now()
                    line.save(update_fields=['physical_quantity', 'remarks', 'counted_by', 'counted_at'])
                except (ValueError, ArithmeticError):
                    pass

        if request.POST.get('action') == 'submit':
            count.status = PhysicalCount.Status.SUBMITTED
            count.completed_at = timezone.now()
            count.save(update_fields=['status', 'completed_at'])
            _log(request.user, AuditLog.Action.UPDATE, f'Physical count {count.count_number} submitted for approval')
            messages.success(request, f'Count {count.count_number} submitted for approval.')
        else:
            messages.success(request, 'Counted quantities saved.')
        return redirect('physical_count_detail', pk=pk)

    return redirect('physical_count_detail', pk=pk)


@login_required
@hms_permission_required('core.approve_stock_count')
def physical_count_approve(request, pk):
    count = get_object_or_404(PhysicalCount, pk=pk, status=PhysicalCount.Status.SUBMITTED)

    if request.method == 'POST':
        action = request.POST.get('action')
        notes = request.POST.get('notes', '').strip()

        if action == 'approve':
            with transaction.atomic():
                adjustments_made = 0
                for line in count.lines.filter(physical_quantity__isnull=False):
                    if line.has_variance:
                        _apply_adjustment(line, count, request.user, notes)
                        adjustments_made += 1
                    line.is_approved = True
                    line.verified_by = request.user
                    line.save(update_fields=['is_approved', 'verified_by'])

                count.status = PhysicalCount.Status.APPROVED
                count.approved_by = request.user
                count.approval_notes = notes
                count.save(update_fields=['status', 'approved_by', 'approval_notes'])

            _log(request.user, AuditLog.Action.APPROVE,
                 f'Physical count {count.count_number} approved — {adjustments_made} adjustment(s) applied')
            messages.success(request, f'Count {count.count_number} approved. {adjustments_made} adjustment(s) applied.')
        elif action == 'reject':
            count.status = PhysicalCount.Status.IN_PROGRESS
            count.save(update_fields=['status'])
            _log(request.user, AuditLog.Action.UPDATE, f'Physical count {count.count_number} sent back for recount: {notes}')
            messages.info(request, f'Count {count.count_number} sent back for recount.')

        return redirect('physical_count_detail', pk=pk)

    return redirect('physical_count_detail', pk=pk)


def _apply_adjustment(line, count, user, reason):
    """Write the counted quantity back into the source domain's own balance
    + native ledger, and record a permanent InventoryAdjustment row."""
    variance = line.variance
    adj_type = InventoryAdjustment.AdjustmentType.INCREASE if variance > 0 else InventoryAdjustment.AdjustmentType.DECREASE

    if line.inventory_item:
        item = line.inventory_item
        item.quantity_in_stock = line.physical_quantity
        item.save(update_fields=['quantity_in_stock'])
        InventoryTransaction.objects.create(
            inventory_item=item, transaction_type=InventoryTransaction.TxType.COUNT_ADJUST,
            quantity_in=max(Decimal('0'), variance), quantity_out=max(Decimal('0'), -variance),
            balance_after=line.physical_quantity, unit_cost=line.unit_cost,
            reference_number=count.count_number,
            notes=f'Physical inventory count adjustment — {count.count_number}. {reason}',
            performed_by=user,
        )
    elif line.medication_batch:
        batch = line.medication_batch
        batch.quantity_available = int(line.physical_quantity)
        batch.save(update_fields=['quantity_available'])
        tx_type = StockTransaction.TxType.ADJUSTMENT_IN if variance > 0 else StockTransaction.TxType.ADJUSTMENT_OUT
        StockTransaction.objects.create(
            medication=batch.medication, batch=batch, transaction_type=tx_type,
            quantity_in=max(0, int(variance)), quantity_out=max(0, int(-variance)),
            balance_after=batch.medication.current_stock, unit_cost=line.unit_cost,
            total_value=abs(variance) * line.unit_cost,
            reference_number=count.count_number,
            notes=f'Physical inventory count adjustment — {count.count_number}. {reason}',
            performed_by=user, transaction_date=timezone.now(),
        )
    elif line.department_stock:
        ds = line.department_stock
        ds.quantity_available = int(line.physical_quantity)
        ds.save(update_fields=['quantity_available'])
        # No dedicated dept-store ledger model exists — the InventoryAdjustment
        # row below (always created) is this domain's permanent record.

    InventoryAdjustment.objects.create(
        physical_count_line=line, domain=count.domain, item_description=line.item_name,
        previous_quantity=line.system_quantity, physical_quantity=line.physical_quantity,
        difference=variance, adjustment_type=adj_type, unit_cost=line.unit_cost,
        adjustment_value=variance * line.unit_cost, reason=reason, approved_by=user,
    )
