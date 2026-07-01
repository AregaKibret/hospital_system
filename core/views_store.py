"""
Enhanced Hospital Store & Inventory Management Views
Covers: batch tracking, transactions, equipment assets, purchase requests, stock counts, reports
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, Count, F
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
import csv

from .models import (
    InventoryItem, InventoryCategory, InventoryBatch, InventoryTransaction,
    EquipmentAsset, EquipmentMaintenance, PurchaseRequest, PurchaseRequestItem,
    PurchaseOrder, StockCountSession, StockCountItem,
    StorageLocation, Supplier, Department, AuditLog,
)
from .decorators import hms_permission_required
from .audit import log_action


def _log(user, action, module, desc):
    """Thin wrapper so views can call log_action without caring about keyword args."""
    try:
        log_action(user, action, module, description=desc)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY ITEM — detail & management
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.read_inventory')
def store_item_detail(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    batches = item.batches.filter(is_active=True).order_by('expiration_date') if item.has_expiry else []
    transactions = item.transactions.select_related('performed_by', 'department').order_by('-transaction_date')[:50]
    context = {
        'item': item,
        'batches': batches,
        'transactions': transactions,
        'expired_batches': item.batches.filter(is_active=True, quantity_available__gt=0,
                           expiration_date__lt=timezone.localdate()).count() if item.has_expiry else 0,
    }
    return render(request, 'store/item_detail.html', context)


@login_required
@hms_permission_required('core.manage_inventory')
def store_item_deactivate(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == 'POST':
        item.is_active = not item.is_active
        item.save(update_fields=['is_active'])
        status = 'activated' if item.is_active else 'deactivated'
        messages.success(request, f"'{item.name}' {status}.")
        _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY, f"Item {status}: {item.name}")
    return redirect('store_item_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY BATCH — receive, list, dispose
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.manage_inventory_batches')
def store_batch_list(request):
    batches = InventoryBatch.objects.select_related('inventory_item', 'supplier', 'received_by')
    item_id = request.GET.get('item')
    status_f = request.GET.get('status', '')
    q = request.GET.get('q', '')

    if item_id:
        batches = batches.filter(inventory_item_id=item_id)
    if q:
        batches = batches.filter(
            Q(inventory_item__name__icontains=q) |
            Q(batch_number__icontains=q) |
            Q(lot_number__icontains=q)
        )
    if status_f == 'active':
        batches = batches.filter(is_active=True, quantity_available__gt=0)
    elif status_f == 'expired':
        batches = batches.filter(is_active=True, expiration_date__lt=timezone.localdate())
    elif status_f == 'near':
        from datetime import timedelta
        threshold = timezone.localdate() + timedelta(days=90)
        batches = batches.filter(is_active=True, expiration_date__lte=threshold,
                                  expiration_date__gte=timezone.localdate())
    elif status_f == 'zero':
        batches = batches.filter(quantity_available=0)

    paginator = Paginator(batches.order_by('expiration_date', 'batch_number'), 40)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'store/batch_list.html', {
        'page_obj': page_obj,
        'status_f': status_f,
        'q': q,
        'item_id': item_id,
    })


@login_required
@hms_permission_required('core.manage_inventory_batches')
def store_batch_receive(request, item_pk):
    item = get_object_or_404(InventoryItem, pk=item_pk)
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    locations = StorageLocation.objects.all().order_by('name')

    if request.method == 'POST':
        p = request.POST
        batch_number = p.get('batch_number', '').strip()
        if not batch_number:
            import uuid
            batch_number = f"B-{timezone.localdate():%Y%m}-{str(uuid.uuid4())[:6].upper()}"

        qty = float(p.get('quantity_received', 0) or 0)
        cost = float(p.get('purchase_price', 0) or 0)

        with transaction.atomic():
            batch = InventoryBatch(
                inventory_item=item,
                batch_number=batch_number,
                lot_number=p.get('lot_number', ''),
                manufacturing_date=p.get('manufacturing_date') or None,
                expiration_date=p.get('expiration_date') or None,
                quantity_received=qty,
                quantity_available=qty,
                purchase_price=cost,
                received_by=request.user,
                purchase_order_ref=p.get('purchase_order_ref', ''),
                invoice_number=p.get('invoice_number', ''),
                notes=p.get('notes', ''),
            )
            if p.get('supplier'):
                batch.supplier_id = int(p['supplier'])
            if p.get('location'):
                batch.location_id = int(p['location'])
            batch.save()

            # Update item stock
            item.quantity_in_stock = float(item.quantity_in_stock) + qty
            item.unit_cost = cost if cost > 0 else item.unit_cost
            item.save(update_fields=['quantity_in_stock', 'unit_cost'])

            # Transaction log
            InventoryTransaction.objects.create(
                inventory_item=item,
                batch=batch,
                transaction_type=InventoryTransaction.TxType.PURCHASE,
                quantity_in=qty,
                quantity_out=0,
                balance_after=item.quantity_in_stock,
                unit_cost=cost if cost > 0 else None,
                reference_number=p.get('purchase_order_ref', ''),
                notes=f"Batch {batch_number} received",
                performed_by=request.user,
            )

            _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                       f"Batch {batch_number} received for {item.name}: qty={qty}")
            messages.success(request, f"Batch {batch_number} received successfully ({qty} {item.unit}).")
        return redirect('store_item_detail', pk=item_pk)

    return render(request, 'store/batch_receive.html', {
        'item': item,
        'suppliers': suppliers,
        'locations': locations,
    })


@login_required
@hms_permission_required('core.manage_inventory_batches')
def store_batch_dispose(request, pk):
    batch = get_object_or_404(InventoryBatch, pk=pk)
    item = batch.inventory_item

    if request.method == 'POST':
        p = request.POST
        qty = float(p.get('quantity', 0) or 0)
        reason = p.get('reason', 'expired')
        notes = p.get('notes', '')

        if qty <= 0 or qty > float(batch.quantity_available):
            messages.error(request, "Invalid quantity for disposal.")
            return redirect('store_batch_dispose', pk=pk)

        with transaction.atomic():
            batch.quantity_available = float(batch.quantity_available) - qty
            if batch.quantity_available <= 0:
                batch.is_active = False
            batch.save(update_fields=['quantity_available', 'is_active'])

            item.quantity_in_stock = float(item.quantity_in_stock) - qty
            item.save(update_fields=['quantity_in_stock'])

            tx_type = (InventoryTransaction.TxType.EXPIRED
                       if reason == 'expired' else InventoryTransaction.TxType.DAMAGED)
            InventoryTransaction.objects.create(
                inventory_item=item,
                batch=batch,
                transaction_type=tx_type,
                quantity_in=0,
                quantity_out=qty,
                balance_after=item.quantity_in_stock,
                notes=f"{reason.title()} disposal — {notes}",
                performed_by=request.user,
            )
            _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                       f"Batch {batch.batch_number} disposed {qty} {item.unit} ({reason})")
            messages.success(request, f"{qty} {item.unit} disposed from batch {batch.batch_number}.")
        return redirect('store_item_detail', pk=item.pk)

    return render(request, 'store/batch_dispose.html', {'batch': batch, 'item': item})


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY TRANSACTIONS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.read_inventory')
def store_transaction_list(request):
    txs = InventoryTransaction.objects.select_related('inventory_item', 'performed_by', 'department')
    q = request.GET.get('q', '')
    tx_type = request.GET.get('type', '')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    if q:
        txs = txs.filter(
            Q(inventory_item__name__icontains=q) |
            Q(reference_number__icontains=q) |
            Q(notes__icontains=q)
        )
    if tx_type:
        txs = txs.filter(transaction_type=tx_type)
    if date_from:
        txs = txs.filter(transaction_date__date__gte=date_from)
    if date_to:
        txs = txs.filter(transaction_date__date__lte=date_to)

    paginator = Paginator(txs.order_by('-transaction_date'), 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'store/transaction_list.html', {
        'page_obj': page_obj,
        'q': q,
        'tx_type': tx_type,
        'date_from': date_from,
        'date_to': date_to,
        'tx_types': InventoryTransaction.TxType.choices,
    })


@login_required
@hms_permission_required('core.issue_inventory')
def store_issue_item(request, item_pk):
    """Issue store items to a department."""
    item = get_object_or_404(InventoryItem, pk=item_pk)
    departments = Department.objects.filter(is_active=True).order_by('name') if hasattr(Department, 'is_active') else Department.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        qty = float(p.get('quantity', 0) or 0)
        dept_id = p.get('department')
        notes = p.get('notes', '')
        ref = p.get('reference', '')

        if qty <= 0:
            messages.error(request, "Quantity must be greater than zero.")
            return redirect('store_issue_item', item_pk=item_pk)
        if float(item.quantity_in_stock) < qty:
            messages.error(request, f"Insufficient stock. Available: {item.quantity_in_stock} {item.unit}.")
            return redirect('store_issue_item', item_pk=item_pk)

        with transaction.atomic():
            item.quantity_in_stock = float(item.quantity_in_stock) - qty
            item.save(update_fields=['quantity_in_stock'])

            tx = InventoryTransaction(
                inventory_item=item,
                transaction_type=InventoryTransaction.TxType.ISSUE,
                quantity_in=0,
                quantity_out=qty,
                balance_after=item.quantity_in_stock,
                reference_number=ref,
                notes=notes,
                performed_by=request.user,
            )
            if dept_id:
                tx.department_id = int(dept_id)
            tx.save()

            _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                       f"Issued {qty} {item.unit} of {item.name} to dept {dept_id}")
            messages.success(request, f"{qty} {item.unit} of {item.name} issued successfully.")
        return redirect('store_item_detail', pk=item_pk)

    return render(request, 'store/issue_item.html', {
        'item': item,
        'departments': departments,
    })


@login_required
@hms_permission_required('core.manage_inventory')
def store_item_adjust(request, item_pk):
    """Manual stock adjustment for a store item."""
    item = get_object_or_404(InventoryItem, pk=item_pk)

    if request.method == 'POST':
        p = request.POST
        adj_type = p.get('adj_type', 'in')
        qty = float(p.get('quantity', 0) or 0)
        notes = p.get('notes', '')
        ref = p.get('reference', '')

        if qty <= 0:
            messages.error(request, "Quantity must be positive.")
            return redirect('store_item_detail', pk=item_pk)

        with transaction.atomic():
            if adj_type == 'in':
                item.quantity_in_stock = float(item.quantity_in_stock) + qty
                tx_type = InventoryTransaction.TxType.ADJUSTMENT_IN
            else:
                if float(item.quantity_in_stock) < qty:
                    messages.error(request, "Cannot reduce below zero.")
                    return redirect('store_item_detail', pk=item_pk)
                item.quantity_in_stock = float(item.quantity_in_stock) - qty
                tx_type = InventoryTransaction.TxType.ADJUSTMENT_OUT

            item.save(update_fields=['quantity_in_stock'])
            InventoryTransaction.objects.create(
                inventory_item=item,
                transaction_type=tx_type,
                quantity_in=qty if adj_type == 'in' else 0,
                quantity_out=0 if adj_type == 'in' else qty,
                balance_after=item.quantity_in_stock,
                reference_number=ref,
                notes=notes,
                performed_by=request.user,
            )
            _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                       f"Adjustment {adj_type} {qty} for {item.name}")
            messages.success(request, f"Stock adjusted by {'+' if adj_type == 'in' else '-'}{qty} {item.unit}.")
        return redirect('store_item_detail', pk=item_pk)

    return render(request, 'store/item_adjust.html', {'item': item})


# ─────────────────────────────────────────────────────────────────────────────
# EQUIPMENT ASSETS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.manage_equipment_assets')
def equipment_list(request):
    assets = EquipmentAsset.objects.select_related('category', 'department', 'supplier')
    q = request.GET.get('q', '')
    status_f = request.GET.get('status', '')
    dept_f = request.GET.get('dept', '')
    cat_f = request.GET.get('cat', '')
    maintenance_due = request.GET.get('maintenance_due', '')

    if q:
        assets = assets.filter(
            Q(name__icontains=q) | Q(asset_code__icontains=q) |
            Q(serial_number__icontains=q) | Q(brand__icontains=q)
        )
    if status_f:
        assets = assets.filter(status=status_f)
    if dept_f:
        assets = assets.filter(department_id=dept_f)
    if cat_f:
        assets = assets.filter(category_id=cat_f)
    if maintenance_due == '1':
        assets = assets.filter(next_maintenance__lte=timezone.localdate())

    # KPIs
    total = EquipmentAsset.objects.count()
    active = EquipmentAsset.objects.filter(status=EquipmentAsset.AssetStatus.ACTIVE).count()
    in_repair = EquipmentAsset.objects.filter(status=EquipmentAsset.AssetStatus.IN_REPAIR).count()
    maint_due = EquipmentAsset.objects.filter(
        next_maintenance__lte=timezone.localdate(), is_active=True
    ).count()

    paginator = Paginator(assets.order_by('name'), 40)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    departments = Department.objects.order_by('name')
    categories = InventoryCategory.objects.order_by('name')

    return render(request, 'store/equipment_list.html', {
        'page_obj': page_obj,
        'q': q, 'status_f': status_f, 'dept_f': dept_f, 'cat_f': cat_f,
        'maintenance_due': maintenance_due,
        'departments': departments,
        'categories': categories,
        'status_choices': EquipmentAsset.AssetStatus.choices,
        'total': total, 'active': active, 'in_repair': in_repair, 'maint_due': maint_due,
    })


@login_required
@hms_permission_required('core.manage_equipment_assets')
def equipment_create(request):
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    categories = InventoryCategory.objects.order_by('name')
    departments = Department.objects.order_by('name')
    items = InventoryItem.objects.filter(item_type=InventoryItem.ItemType.EQUIPMENT, is_active=True).order_by('name')

    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        asset_code = p.get('asset_code', '').strip()

        if not name or not asset_code:
            messages.error(request, "Name and Asset Code are required.")
            return redirect('equipment_create')

        if EquipmentAsset.objects.filter(asset_code=asset_code).exists():
            messages.error(request, f"Asset code '{asset_code}' already exists.")
            return redirect('equipment_create')

        asset = EquipmentAsset(
            name=name,
            asset_code=asset_code,
            serial_number=p.get('serial_number', ''),
            model_number=p.get('model_number', ''),
            brand=p.get('brand', ''),
            status=p.get('status', EquipmentAsset.AssetStatus.ACTIVE),
            purchase_date=p.get('purchase_date') or None,
            purchase_price=p.get('purchase_price') or None,
            invoice_number=p.get('invoice_number', ''),
            warranty_expiry=p.get('warranty_expiry') or None,
            next_maintenance=p.get('next_maintenance') or None,
            location_detail=p.get('location_detail', ''),
            notes=p.get('notes', ''),
            created_by=request.user,
        )
        if p.get('category'):
            asset.category_id = int(p['category'])
        if p.get('department'):
            asset.department_id = int(p['department'])
        if p.get('supplier'):
            asset.supplier_id = int(p['supplier'])
        if p.get('inventory_item'):
            asset.inventory_item_id = int(p['inventory_item'])
        if p.get('purchase_order'):
            asset.purchase_order_id = int(p['purchase_order'])
        asset.save()

        _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                   f"Equipment asset created: {asset_code} {name}")
        messages.success(request, f"Asset '{name}' ({asset_code}) created.")
        return redirect('equipment_detail', pk=asset.pk)

    return render(request, 'store/equipment_form.html', {
        'action': 'Register',
        'suppliers': suppliers,
        'categories': categories,
        'departments': departments,
        'items': items,
        'status_choices': EquipmentAsset.AssetStatus.choices,
    })


@login_required
@hms_permission_required('core.manage_equipment_assets')
def equipment_detail(request, pk):
    asset = get_object_or_404(EquipmentAsset.objects.select_related(
        'category', 'department', 'supplier', 'created_by', 'inventory_item'), pk=pk)
    maintenance_records = asset.maintenance_records.select_related('recorded_by').order_by('-maintenance_date')[:20]
    return render(request, 'store/equipment_detail.html', {
        'asset': asset,
        'maintenance_records': maintenance_records,
        'maintenance_types': EquipmentMaintenance.MaintenanceType.choices,
        'status_choices': EquipmentAsset.AssetStatus.choices,
    })


@login_required
@hms_permission_required('core.manage_equipment_assets')
def equipment_edit(request, pk):
    asset = get_object_or_404(EquipmentAsset, pk=pk)
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    categories = InventoryCategory.objects.order_by('name')
    departments = Department.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        asset.name = p.get('name', asset.name)
        asset.serial_number = p.get('serial_number', '')
        asset.model_number = p.get('model_number', '')
        asset.brand = p.get('brand', '')
        asset.status = p.get('status', asset.status)
        asset.purchase_date = p.get('purchase_date') or None
        asset.purchase_price = p.get('purchase_price') or None
        asset.invoice_number = p.get('invoice_number', '')
        asset.warranty_expiry = p.get('warranty_expiry') or None
        asset.next_maintenance = p.get('next_maintenance') or None
        asset.location_detail = p.get('location_detail', '')
        asset.notes = p.get('notes', '')
        asset.category_id = int(p['category']) if p.get('category') else None
        asset.department_id = int(p['department']) if p.get('department') else None
        asset.supplier_id = int(p['supplier']) if p.get('supplier') else None
        asset.save()

        _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                   f"Equipment asset updated: {asset.asset_code}")
        messages.success(request, "Asset updated successfully.")
        return redirect('equipment_detail', pk=pk)

    return render(request, 'store/equipment_form.html', {
        'action': 'Edit',
        'asset': asset,
        'suppliers': suppliers,
        'categories': categories,
        'departments': departments,
        'status_choices': EquipmentAsset.AssetStatus.choices,
    })


@login_required
@hms_permission_required('core.manage_equipment_assets')
def equipment_maintenance_add(request, asset_pk):
    asset = get_object_or_404(EquipmentAsset, pk=asset_pk)

    if request.method == 'POST':
        p = request.POST
        record = EquipmentMaintenance(
            asset=asset,
            maintenance_type=p.get('maintenance_type', EquipmentMaintenance.MaintenanceType.PREVENTIVE),
            description=p.get('description', ''),
            technician_name=p.get('technician_name', ''),
            technician_contact=p.get('technician_contact', ''),
            cost=p.get('cost') or None,
            maintenance_date=p.get('maintenance_date') or timezone.localdate(),
            next_due=p.get('next_due') or None,
            status_after=p.get('status_after', ''),
            notes=p.get('notes', ''),
            recorded_by=request.user,
        )
        record.save()

        # Update asset maintenance dates and optionally status
        asset.last_maintenance = record.maintenance_date
        if record.next_due:
            asset.next_maintenance = record.next_due
        if record.status_after:
            asset.status = record.status_after
        asset.save(update_fields=['last_maintenance', 'next_maintenance', 'status'])

        _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                   f"Maintenance recorded for {asset.asset_code}: {record.maintenance_type}")
        messages.success(request, "Maintenance record added.")
    return redirect('equipment_detail', pk=asset_pk)


# ─────────────────────────────────────────────────────────────────────────────
# PURCHASE REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.create_purchase_request')
def purchase_request_list(request):
    prs = PurchaseRequest.objects.select_related('department', 'requested_by', 'approved_by')
    status_f = request.GET.get('status', '')
    priority_f = request.GET.get('priority', '')
    dept_f = request.GET.get('dept', '')
    q = request.GET.get('q', '')

    if status_f:
        prs = prs.filter(status=status_f)
    if priority_f:
        prs = prs.filter(priority=priority_f)
    if dept_f:
        prs = prs.filter(department_id=dept_f)
    if q:
        prs = prs.filter(
            Q(request_number__icontains=q) |
            Q(reason__icontains=q) |
            Q(items__item_name__icontains=q)
        ).distinct()

    # KPIs
    pending_count = PurchaseRequest.objects.filter(status=PurchaseRequest.Status.PENDING).count()
    approved_count = PurchaseRequest.objects.filter(status=PurchaseRequest.Status.APPROVED).count()
    my_requests = PurchaseRequest.objects.filter(requested_by=request.user).count()

    paginator = Paginator(prs.order_by('-request_date'), 30)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    departments = Department.objects.order_by('name')
    return render(request, 'store/purchase_request_list.html', {
        'page_obj': page_obj,
        'status_f': status_f, 'priority_f': priority_f, 'dept_f': dept_f, 'q': q,
        'departments': departments,
        'status_choices': PurchaseRequest.Status.choices,
        'priority_choices': PurchaseRequest.Priority.choices,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'my_requests': my_requests,
    })


@login_required
@hms_permission_required('core.create_purchase_request')
def purchase_request_create(request):
    departments = Department.objects.order_by('name')
    items = InventoryItem.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        p = request.POST
        dept_id = p.get('department')
        if not dept_id:
            messages.error(request, "Department is required.")
            return redirect('purchase_request_create')

        pr = PurchaseRequest(
            department_id=int(dept_id),
            requested_by=request.user,
            status=PurchaseRequest.Status.PENDING,
            priority=p.get('priority', PurchaseRequest.Priority.NORMAL),
            reason=p.get('reason', ''),
            required_by=p.get('required_by') or None,
        )
        pr.save()

        # Save line items
        item_ids = request.POST.getlist('item_id[]')
        item_names = request.POST.getlist('item_name[]')
        quantities = request.POST.getlist('quantity[]')
        units = request.POST.getlist('unit[]')
        costs = request.POST.getlist('estimated_cost[]')
        notes_list = request.POST.getlist('item_notes[]')

        for i, qty_str in enumerate(quantities):
            try:
                qty = float(qty_str or 0)
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            ri = PurchaseRequestItem(
                purchase_request=pr,
                item_name=item_names[i] if i < len(item_names) else '',
                quantity_requested=qty,
                unit=units[i] if i < len(units) else '',
                notes=notes_list[i] if i < len(notes_list) else '',
            )
            if i < len(item_ids) and item_ids[i]:
                try:
                    ri.inventory_item_id = int(item_ids[i])
                except ValueError:
                    pass
            if i < len(costs) and costs[i]:
                try:
                    ri.estimated_unit_cost = float(costs[i])
                except ValueError:
                    pass
            ri.save()

        _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                   f"Purchase request {pr.request_number} created")
        messages.success(request, f"Purchase request {pr.request_number} submitted.")
        return redirect('purchase_request_detail', pk=pr.pk)

    return render(request, 'store/purchase_request_form.html', {
        'departments': departments,
        'items': items,
        'priority_choices': PurchaseRequest.Priority.choices,
    })


@login_required
@hms_permission_required('core.create_purchase_request')
def purchase_request_detail(request, pk):
    pr = get_object_or_404(
        PurchaseRequest.objects.select_related('department', 'requested_by', 'approved_by', 'purchase_order'),
        pk=pk
    )
    line_items = pr.items.select_related('inventory_item').all()
    return render(request, 'store/purchase_request_detail.html', {
        'pr': pr,
        'line_items': line_items,
    })


@login_required
@hms_permission_required('core.approve_purchase_request')
def purchase_request_approve(request, pk):
    pr = get_object_or_404(PurchaseRequest, pk=pk, status=PurchaseRequest.Status.PENDING)

    if request.method == 'POST':
        action = request.POST.get('action')
        notes = request.POST.get('approval_notes', '')
        if action == 'approve':
            pr.status = PurchaseRequest.Status.APPROVED
            # Update approved quantities from form
            for item in pr.items.all():
                qa_key = f'qty_approved_{item.pk}'
                if request.POST.get(qa_key):
                    try:
                        item.quantity_approved = float(request.POST[qa_key])
                        item.save(update_fields=['quantity_approved'])
                    except ValueError:
                        pass
            msg = f"Purchase request {pr.request_number} approved."
        else:
            pr.status = PurchaseRequest.Status.REJECTED
            msg = f"Purchase request {pr.request_number} rejected."

        pr.approved_by = request.user
        pr.approval_notes = notes
        pr.approval_date = timezone.now()
        pr.save(update_fields=['status', 'approved_by', 'approval_notes', 'approval_date'])

        _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                   f"PR {pr.request_number} {pr.status}")
        messages.success(request, msg)
    return redirect('purchase_request_detail', pk=pk)


@login_required
@hms_permission_required('core.approve_purchase_request')
def purchase_request_convert(request, pk):
    """Convert an approved purchase request into a purchase order."""
    pr = get_object_or_404(PurchaseRequest, pk=pk, status=PurchaseRequest.Status.APPROVED)

    if request.method == 'POST':
        supplier_name = request.POST.get('supplier_name', 'TBD')
        with transaction.atomic():
            po = PurchaseOrder(
                supplier_name=supplier_name,
                supplier_contact=request.POST.get('supplier_contact', ''),
                created_by=request.user,
                status=PurchaseOrder.Status.DRAFT,
                notes=f"Generated from PR {pr.request_number}",
                expected_delivery=request.POST.get('expected_delivery') or None,
            )
            po.save()

            total = 0
            for item in pr.items.all():
                qty = float(item.quantity_approved or item.quantity_requested)
                cost = float(item.estimated_unit_cost or 0)
                from .models import PurchaseOrderItem
                poi = PurchaseOrderItem(
                    purchase_order=po,
                    inventory_item=item.inventory_item,
                    item_name=item.item_name,
                    quantity_ordered=qty,
                    unit_cost=cost,
                )
                poi.save()
                total += qty * cost

            po.total_amount = total
            po.save(update_fields=['total_amount'])

            pr.status = PurchaseRequest.Status.CONVERTED
            pr.purchase_order = po
            pr.save(update_fields=['status', 'purchase_order'])

            _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                       f"PR {pr.request_number} converted to PO {po.po_number}")
            messages.success(request, f"PO {po.po_number} created from PR {pr.request_number}.")
        return redirect('purchase_order_detail', pk=po.pk)

    return render(request, 'store/purchase_request_convert.html', {
        'pr': pr,
        'suppliers': Supplier.objects.filter(is_active=True).order_by('name'),
    })


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL STOCK COUNT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.perform_stock_count')
def stock_count_list(request):
    sessions = StockCountSession.objects.select_related('created_by', 'approved_by', 'category', 'location')
    status_f = request.GET.get('status', '')
    if status_f:
        sessions = sessions.filter(status=status_f)

    counts = {
        'planned': StockCountSession.objects.filter(status=StockCountSession.Status.PLANNED).count(),
        'in_progress': StockCountSession.objects.filter(status=StockCountSession.Status.IN_PROGRESS).count(),
        'submitted': StockCountSession.objects.filter(status=StockCountSession.Status.SUBMITTED).count(),
    }

    paginator = Paginator(sessions.order_by('-created_at'), 30)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'store/stock_count_list.html', {
        'page_obj': page_obj,
        'status_f': status_f,
        'status_choices': StockCountSession.Status.choices,
        'counts': counts,
    })


@login_required
@hms_permission_required('core.perform_stock_count')
def stock_count_create(request):
    categories = InventoryCategory.objects.order_by('name')
    locations = StorageLocation.objects.all().order_by('name')

    if request.method == 'POST':
        p = request.POST
        session = StockCountSession(
            count_type=p.get('count_type', StockCountSession.CountType.FULL),
            status=StockCountSession.Status.IN_PROGRESS,
            notes=p.get('notes', ''),
            created_by=request.user,
        )
        if p.get('category'):
            session.category_id = int(p['category'])
        if p.get('location'):
            session.location_id = int(p['location'])
        session.save()

        # Auto-populate items from inventory
        items_qs = InventoryItem.objects.filter(is_active=True)
        if session.category:
            items_qs = items_qs.filter(category=session.category)

        bulk_items = []
        for item in items_qs:
            bulk_items.append(StockCountItem(
                count_session=session,
                inventory_item=item,
                system_quantity=item.quantity_in_stock,
            ))
        StockCountItem.objects.bulk_create(bulk_items, ignore_conflicts=True)

        _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                   f"Stock count session {session.count_number} created")
        messages.success(request, f"Stock count {session.count_number} started with {len(bulk_items)} items.")
        return redirect('stock_count_detail', pk=session.pk)

    return render(request, 'store/stock_count_create.html', {
        'categories': categories,
        'locations': locations,
        'count_types': StockCountSession.CountType.choices,
    })


@login_required
@hms_permission_required('core.perform_stock_count')
def stock_count_detail(request, pk):
    session = get_object_or_404(StockCountSession.objects.select_related(
        'created_by', 'approved_by', 'category'), pk=pk)
    count_items = session.items.select_related('inventory_item').order_by('inventory_item__name')
    return render(request, 'store/stock_count_detail.html', {
        'session': session,
        'count_items': count_items,
        'can_approve': request.user.has_perm('core.approve_stock_count'),
    })


@login_required
@hms_permission_required('core.perform_stock_count')
def stock_count_enter(request, pk):
    """Save counted quantities for a stock count session."""
    session = get_object_or_404(StockCountSession, pk=pk)
    if session.status not in [StockCountSession.Status.IN_PROGRESS]:
        messages.error(request, "Count session is not in progress.")
        return redirect('stock_count_detail', pk=pk)

    if request.method == 'POST':
        for count_item in session.items.all():
            key = f'counted_{count_item.pk}'
            val = request.POST.get(key, '')
            if val != '':
                try:
                    count_item.counted_quantity = float(val)
                    note_key = f'note_{count_item.pk}'
                    count_item.notes = request.POST.get(note_key, '')
                    count_item.save(update_fields=['counted_quantity', 'notes'])
                except ValueError:
                    pass

        action = request.POST.get('action', 'save')
        if action == 'submit':
            session.status = StockCountSession.Status.SUBMITTED
            session.completed_at = timezone.now()
            session.save(update_fields=['status', 'completed_at'])
            messages.success(request, f"Count {session.count_number} submitted for approval.")
            return redirect('stock_count_detail', pk=pk)

        messages.success(request, "Count quantities saved.")
        return redirect('stock_count_detail', pk=pk)

    return redirect('stock_count_detail', pk=pk)


@login_required
@hms_permission_required('core.approve_stock_count')
def stock_count_approve(request, pk):
    session = get_object_or_404(StockCountSession, pk=pk, status=StockCountSession.Status.SUBMITTED)

    if request.method == 'POST':
        action = request.POST.get('action')
        notes = request.POST.get('notes', '')

        if action == 'approve':
            with transaction.atomic():
                for count_item in session.items.filter(counted_quantity__isnull=False):
                    variance = float(count_item.counted_quantity) - float(count_item.system_quantity)
                    if variance != 0:
                        item = count_item.inventory_item
                        item.quantity_in_stock = count_item.counted_quantity
                        item.save(update_fields=['quantity_in_stock'])

                        tx_type = (InventoryTransaction.TxType.COUNT_ADJUST)
                        InventoryTransaction.objects.create(
                            inventory_item=item,
                            transaction_type=tx_type,
                            quantity_in=max(0, variance),
                            quantity_out=max(0, -variance),
                            balance_after=count_item.counted_quantity,
                            reference_number=session.count_number,
                            notes=f"Physical count adjustment — {session.count_number}",
                            performed_by=request.user,
                        )
                    count_item.is_approved = True
                    count_item.save(update_fields=['is_approved'])

                session.status = StockCountSession.Status.APPROVED
                session.approved_by = request.user
                session.approval_notes = notes
                session.save(update_fields=['status', 'approved_by', 'approval_notes'])

                _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                           f"Stock count {session.count_number} approved and applied")
                messages.success(request, f"Count {session.count_number} approved. Inventory updated.")
        else:
            session.status = StockCountSession.Status.IN_PROGRESS
            session.save(update_fields=['status'])
            messages.info(request, "Count returned for re-counting.")

    return redirect('stock_count_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# STORE REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@hms_permission_required('core.view_store_reports')
def report_store_stock_on_hand(request):
    """Current stock levels for all inventory items."""
    items = InventoryItem.objects.select_related('category', 'supplier', 'storage_location').filter(is_active=True)
    cat_f = request.GET.get('cat', '')
    type_f = request.GET.get('type', '')
    status_f = request.GET.get('status', '')

    if cat_f:
        items = items.filter(category_id=cat_f)
    if type_f:
        items = items.filter(item_type=type_f)
    if status_f == 'low':
        items = [i for i in items if i.is_low_stock and not i.is_out_of_stock]
    elif status_f == 'out':
        items = [i for i in items if i.is_out_of_stock]
    elif status_f == 'ok':
        items = [i for i in items if not i.is_low_stock]

    total_value = sum(i.inventory_value for i in (items if isinstance(items, list) else items))
    low_count = InventoryItem.objects.filter(is_active=True).count()

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="store_stock_on_hand.csv"'
        writer = csv.writer(response)
        writer.writerow(['Item', 'Type', 'Category', 'SKU', 'Unit', 'In Stock', 'Reorder Level', 'Unit Cost', 'Value', 'Status'])
        for item in items:
            writer.writerow([
                item.name, item.item_type, item.category or '', item.sku,
                item.unit, item.quantity_in_stock, item.reorder_level,
                item.unit_cost, item.inventory_value, item.stock_status,
            ])
        return response

    paginator = Paginator(list(items) if isinstance(items, list) else items.order_by('name'), 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'store/reports/stock_on_hand.html', {
        'page_obj': page_obj,
        'categories': InventoryCategory.objects.order_by('name'),
        'type_choices': InventoryItem.ItemType.choices,
        'cat_f': cat_f, 'type_f': type_f, 'status_f': status_f,
        'total_value': total_value,
    })


@login_required
@hms_permission_required('core.view_store_reports')
def report_store_low_stock(request):
    items = InventoryItem.objects.filter(
        is_active=True, quantity_in_stock__lte=F('reorder_level')
    ).select_related('category', 'supplier').order_by('quantity_in_stock')

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="low_stock.csv"'
        writer = csv.writer(response)
        writer.writerow(['Item', 'Type', 'Category', 'In Stock', 'Reorder Level', 'Supplier'])
        for item in items:
            writer.writerow([item.name, item.item_type, item.category or '',
                              item.quantity_in_stock, item.reorder_level, item.supplier or item.supplier_name])
        return response

    return render(request, 'store/reports/low_stock.html', {'items': items})


@login_required
@hms_permission_required('core.view_store_reports')
def report_store_expiry(request):
    """Batches by expiry status."""
    from datetime import timedelta
    today = timezone.localdate()
    batches = InventoryBatch.objects.select_related('inventory_item', 'supplier').filter(
        is_active=True, quantity_available__gt=0
    )
    filter_f = request.GET.get('filter', 'all')
    if filter_f == 'expired':
        batches = batches.filter(expiration_date__lt=today)
    elif filter_f == '30':
        batches = batches.filter(expiration_date__gte=today, expiration_date__lte=today + timedelta(30))
    elif filter_f == '60':
        batches = batches.filter(expiration_date__gte=today, expiration_date__lte=today + timedelta(60))
    elif filter_f == '90':
        batches = batches.filter(expiration_date__gte=today, expiration_date__lte=today + timedelta(90))

    return render(request, 'store/reports/expiry.html', {
        'batches': batches.order_by('expiration_date'),
        'filter_f': filter_f,
        'today': today,
    })


@login_required
@hms_permission_required('core.view_store_reports')
def report_store_valuation(request):
    """Inventory valuation report."""
    items = InventoryItem.objects.filter(is_active=True).select_related('category')
    by_type = {}
    grand_total = 0
    for item in items:
        val = float(item.inventory_value)
        by_type.setdefault(item.item_type, {'label': item.get_item_type_display(), 'value': 0, 'count': 0})
        by_type[item.item_type]['value'] += val
        by_type[item.item_type]['count'] += 1
        grand_total += val

    return render(request, 'store/reports/valuation.html', {
        'by_type': sorted(by_type.values(), key=lambda x: -x['value']),
        'grand_total': grand_total,
        'items': items.order_by('item_type', 'name'),
    })


@login_required
@hms_permission_required('core.view_store_reports')
def report_store_transactions(request):
    """Stock movement report."""
    txs = InventoryTransaction.objects.select_related('inventory_item', 'performed_by', 'department')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    tx_type = request.GET.get('type', '')
    item_id = request.GET.get('item', '')

    if date_from:
        txs = txs.filter(transaction_date__date__gte=date_from)
    if date_to:
        txs = txs.filter(transaction_date__date__lte=date_to)
    if tx_type:
        txs = txs.filter(transaction_type=tx_type)
    if item_id:
        txs = txs.filter(inventory_item_id=item_id)

    total_in = txs.aggregate(s=Sum('quantity_in'))['s'] or 0
    total_out = txs.aggregate(s=Sum('quantity_out'))['s'] or 0

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="store_transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Item', 'Type', 'In', 'Out', 'Balance', 'Reference', 'Dept', 'By'])
        for tx in txs.order_by('-transaction_date'):
            writer.writerow([
                tx.transaction_date.strftime('%Y-%m-%d %H:%M'),
                tx.inventory_item.name, tx.transaction_type,
                tx.quantity_in, tx.quantity_out, tx.balance_after,
                tx.reference_number, tx.department or '', tx.performed_by.get_full_name(),
            ])
        return response

    paginator = Paginator(txs.order_by('-transaction_date'), 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'store/reports/transactions.html', {
        'page_obj': page_obj,
        'date_from': date_from, 'date_to': date_to,
        'tx_type': tx_type, 'item_id': item_id,
        'tx_types': InventoryTransaction.TxType.choices,
        'items': InventoryItem.objects.filter(is_active=True).order_by('name'),
        'total_in': total_in, 'total_out': total_out,
    })


@login_required
@hms_permission_required('core.view_store_reports')
def report_store_equipment(request):
    """Equipment assets report."""
    assets = EquipmentAsset.objects.select_related('category', 'department', 'supplier')
    status_f = request.GET.get('status', '')
    if status_f:
        assets = assets.filter(status=status_f)

    total_value = assets.aggregate(v=Sum('purchase_price'))['v'] or 0
    by_status = {s: assets.filter(status=s).count() for s, _ in EquipmentAsset.AssetStatus.choices}
    maint_due = assets.filter(next_maintenance__lte=timezone.localdate()).count()

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="equipment_assets.csv"'
        writer = csv.writer(response)
        writer.writerow(['Code', 'Name', 'Brand', 'Serial', 'Category', 'Dept', 'Status',
                         'Purchase Date', 'Purchase Price', 'Warranty Expiry', 'Next Maintenance'])
        for a in assets.order_by('asset_code'):
            writer.writerow([
                a.asset_code, a.name, a.brand, a.serial_number,
                a.category or '', a.department or '', a.status,
                a.purchase_date or '', a.purchase_price or '', a.warranty_expiry or '', a.next_maintenance or '',
            ])
        return response

    paginator = Paginator(assets.order_by('department__name', 'name'), 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'store/reports/equipment.html', {
        'page_obj': page_obj,
        'status_choices': EquipmentAsset.AssetStatus.choices,
        'status_f': status_f,
        'total_value': total_value,
        'by_status': by_status,
        'maint_due': maint_due,
    })
