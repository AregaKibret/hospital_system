"""Department Pharmacy / Temporary Stores — views."""

import csv
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Department, DepartmentStock, DepartmentStockBatch, DepartmentStore,
    DepartmentTransfer, DepartmentTransferItem, DepartmentUsage,
    Invoice, InvoiceItem, InventoryItem, InventoryTransaction,
    Medication, MedicationBatch, Patient, StockTransaction,
    TransferRequest, TransferRequestItem, Visit,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _req_num():
    n = (TransferRequest.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    return f"REQ-{n:06d}"


def _txn_num():
    n = (DepartmentTransfer.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    return f"TRF-{n:06d}"


def _usage_num():
    n = (DepartmentUsage.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    return f"USE-{n:07d}"


def _add_dept_stock(dept_store, medication, source_batch, quantity):
    """Add qty to a dept store's stock, creating DepartmentStock and DepartmentStockBatch as needed."""
    stock, _ = DepartmentStock.objects.get_or_create(
        department_store=dept_store, medication=medication,
        defaults={'quantity_available': 0, 'minimum_quantity': max(medication.reorder_level // 4, 5)},
    )
    stock.quantity_available += quantity
    stock.save()

    if source_batch:
        db_batch, created = DepartmentStockBatch.objects.get_or_create(
            dept_stock=stock,
            source_batch=source_batch,
            batch_number=source_batch.batch_number,
            defaults={
                'expiration_date': source_batch.expiration_date,
                'quantity_available': 0,
                'received_date': date.today(),
            },
        )
        db_batch.quantity_available += quantity
        db_batch.save()
    return stock


def _deduct_dept_stock(dept_stock, quantity, dept_batch=None):
    """Deduct qty from dept stock (FIFO if no specific batch given)."""
    if dept_batch:
        dept_batch.quantity_available = max(0, dept_batch.quantity_available - quantity)
        dept_batch.save()
    else:
        # FIFO by expiry date
        remaining = quantity
        for b in dept_stock.batches.filter(is_active=True, quantity_available__gt=0).order_by('expiration_date'):
            take = min(b.quantity_available, remaining)
            b.quantity_available -= take
            b.save()
            remaining -= take
            if remaining <= 0:
                break

    dept_stock.quantity_available = max(0, dept_stock.quantity_available - quantity)
    dept_stock.save()


# ── System-wide dashboard ─────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_inventory')
def dept_pharmacy_dashboard(request):
    stores    = DepartmentStore.objects.filter(is_active=True).prefetch_related('stock_items')
    pending_requests = TransferRequest.objects.filter(status=TransferRequest.Status.PENDING).select_related('requesting_store', 'requested_by').order_by('-request_date')[:10]
    pending_transfers = DepartmentTransfer.objects.filter(status=DepartmentTransfer.Status.PENDING).select_related('dest_store', 'prepared_by').order_by('-created_at')[:10]

    today   = date.today()
    d30     = today + timedelta(days=30)
    near_expiry = DepartmentStockBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__gt=today, expiration_date__lte=d30,
    ).select_related('dept_stock__department_store', 'dept_stock__medication')[:10]

    expired = DepartmentStockBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__lt=today,
    ).select_related('dept_stock__department_store', 'dept_stock__medication').count()

    store_summaries = []
    for s in stores:
        items = list(s.stock_items.all())
        store_summaries.append({
            'store': s,
            'total_meds': len(items),
            'low_stock': sum(1 for i in items if i.is_low_stock),
            'out_of_stock': sum(1 for i in items if i.is_out_of_stock),
        })

    recent_usage = DepartmentUsage.objects.select_related('department_store', 'medication', 'responsible_staff').order_by('-usage_date')[:10]

    return render(request, 'dept_pharmacy/dashboard.html', {
        'stores':             stores,
        'store_summaries':    store_summaries,
        'pending_requests':   pending_requests,
        'pending_transfers':  pending_transfers,
        'near_expiry':        near_expiry,
        'expired_count':      expired,
        'recent_usage':       recent_usage,
        'today':              today,
    })


# ── Department Store CRUD ─────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_inventory')
def dept_store_list(request):
    stores = DepartmentStore.objects.filter(is_active=True)
    return render(request, 'dept_pharmacy/store_list.html', {'stores': stores})


@hms_permission_required('core.manage_dept_stores')
def dept_store_create(request):
    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        if not name:
            messages.error(request, 'Store name is required.')
        else:
            s = DepartmentStore.objects.create(
                name=name,
                store_type=p.get('store_type', DepartmentStore.StoreType.OTHER),
                location=p.get('location', '').strip(),
                phone=p.get('phone', '').strip(),
                notes=p.get('notes', '').strip(),
            )
            messages.success(request, f'Department store "{s.name}" created.')
            return redirect('dept_store_detail', store_id=s.id)

    return render(request, 'dept_pharmacy/store_form.html', {
        'action': 'Create', 'store_types': DepartmentStore.StoreType.choices, 'post': request.POST,
    })


@hms_permission_required('core.manage_dept_stores')
def dept_store_edit(request, store_id):
    store = get_object_or_404(DepartmentStore, id=store_id)
    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        if not name:
            messages.error(request, 'Store name is required.')
        else:
            store.name       = name
            store.store_type = p.get('store_type', store.store_type)
            store.location   = p.get('location', '').strip()
            store.phone      = p.get('phone', '').strip()
            store.notes      = p.get('notes', '').strip()
            store.save()
            messages.success(request, f'"{store.name}" updated.')
            return redirect('dept_store_detail', store_id=store.id)

    return render(request, 'dept_pharmacy/store_form.html', {
        'action': 'Edit', 'store': store, 'store_types': DepartmentStore.StoreType.choices,
    })


@hms_permission_required('core.view_dept_inventory')
def dept_store_detail(request, store_id):
    store = get_object_or_404(DepartmentStore, id=store_id)
    today = date.today()
    d30   = today + timedelta(days=30)

    stock_items = store.stock_items.select_related('medication', 'medication__inventory_item__category').order_by('medication__brand_name')
    near_expiry = DepartmentStockBatch.objects.filter(
        dept_stock__department_store=store, is_active=True, quantity_available__gt=0,
        expiration_date__gt=today, expiration_date__lte=d30,
    ).select_related('dept_stock__medication').order_by('expiration_date')
    expired = DepartmentStockBatch.objects.filter(
        dept_stock__department_store=store, is_active=True, quantity_available__gt=0,
        expiration_date__lt=today,
    ).select_related('dept_stock__medication')
    recent_requests  = store.transfer_requests.select_related('requested_by').order_by('-request_date')[:5]
    recent_usage     = store.usages.select_related('medication', 'responsible_staff', 'patient').order_by('-usage_date')[:10]
    pending_transfers = DepartmentTransfer.objects.filter(
        dest_store=store, status=DepartmentTransfer.Status.PENDING,
    ).select_related('prepared_by')

    return render(request, 'dept_pharmacy/store_detail.html', {
        'store':              store,
        'stock_items':        stock_items,
        'near_expiry':        near_expiry,
        'expired':            expired,
        'recent_requests':    recent_requests,
        'recent_usage':       recent_usage,
        'pending_transfers':  pending_transfers,
        'today':              today,
        'low_stock':          [s for s in stock_items if s.is_low_stock],
        'out_of_stock':       [s for s in stock_items if s.is_out_of_stock],
    })


# ── Transfer Requests ─────────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_inventory')
def transfer_request_list(request):
    qs = TransferRequest.objects.select_related('requesting_store', 'requested_by', 'approved_by', 'patient')

    status      = request.GET.get('status', '')
    store_id    = request.GET.get('store', '')
    priority    = request.GET.get('priority', '')
    req_type    = request.GET.get('req_type', '')

    if status:    qs = qs.filter(status=status)
    if store_id:  qs = qs.filter(requesting_store_id=store_id)
    if priority:  qs = qs.filter(priority=priority)
    if req_type:  qs = qs.filter(request_type=req_type)

    paginator = Paginator(qs, 25)
    return render(request, 'dept_pharmacy/request_list.html', {
        'page_obj':    paginator.get_page(request.GET.get('page')),
        'stores':      DepartmentStore.objects.filter(is_active=True),
        'statuses':    TransferRequest.Status.choices,
        'priorities':  TransferRequest.Priority.choices,
        'req_types':   TransferRequest.RequestType.choices,
        'status':      status,
        'store_id':    store_id,
        'priority':    priority,
        'req_type':    req_type,
    })


@hms_permission_required('core.request_medication_transfer')
def transfer_request_create(request):
    stores      = DepartmentStore.objects.filter(is_active=True)
    medications = Medication.objects.filter(inventory_item__is_active=True).order_by('brand_name')
    consumables = InventoryItem.objects.filter(is_active=True).order_by('name')
    from django.contrib.auth import get_user_model
    from .models import Doctor as DoctorModel
    doctors = get_user_model().objects.filter(
        id__in=DoctorModel.objects.values_list('user_id', flat=True)
    ).order_by('last_name')

    if request.method == 'POST':
        p            = request.POST
        store_id     = p.get('store')
        priority     = p.get('priority', TransferRequest.Priority.NORMAL)
        required_by  = p.get('required_by') or None
        notes        = p.get('notes', '').strip()
        req_type     = p.get('request_type', TransferRequest.RequestType.DEPT_STOCK)
        patient_id   = p.get('patient_id', '').strip()
        visit_id     = p.get('visit_id', '').strip()
        doctor_id    = p.get('requesting_doctor', '').strip()
        clinical_reason = p.get('clinical_reason', '').strip()

        # Collect medication lines: med_X / qty_X / notes_X
        med_items = []
        idx = 0
        while True:
            med_id = p.get(f'med_{idx}')
            qty    = p.get(f'qty_{idx}')
            if med_id is None:
                break
            if med_id and qty:
                med_items.append((int(med_id), int(qty), p.get(f'notes_{idx}', '').strip()))
            idx += 1

        # Collect consumable lines: item_X / item_qty_X / item_notes_X
        consumable_items = []
        idx = 0
        while True:
            item_id = p.get(f'item_{idx}')
            qty     = p.get(f'item_qty_{idx}')
            if item_id is None:
                break
            if item_id and qty:
                consumable_items.append((int(item_id), int(qty), p.get(f'item_notes_{idx}', '').strip()))
            idx += 1

        store = DepartmentStore.objects.filter(id=store_id).first() if store_id else None
        patient = Patient.objects.filter(id=patient_id).first() if patient_id else None
        visit   = Visit.objects.filter(id=visit_id).first() if visit_id else None

        errors = []
        if not store:
            errors.append('Please select a requesting department.')
        if not med_items and not consumable_items:
            errors.append('Add at least one medication or consumable to the request.')
        if req_type == TransferRequest.RequestType.PATIENT_SPECIFIC and not patient:
            errors.append('A patient must be selected for patient-specific requests.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            initial_status = (
                TransferRequest.Status.PENDING_WARD_APPROVAL if store.requires_ward_supervisor_approval
                else TransferRequest.Status.PENDING
            )
            req = TransferRequest.objects.create(
                request_number=_req_num(),
                requesting_store=store,
                status=initial_status,
                priority=priority,
                required_by=required_by,
                notes=notes,
                requested_by=request.user,
                request_type=req_type,
                patient=patient,
                visit=visit,
                requesting_doctor_id=int(doctor_id) if doctor_id else None,
                clinical_reason=clinical_reason,
            )
            for med_id, qty, item_notes in med_items:
                med = Medication.objects.get(id=med_id)
                TransferRequestItem.objects.create(
                    transfer_request=req,
                    medication_id=med_id,
                    quantity_requested=qty,
                    notes=item_notes,
                    unit_price=med.selling_price,
                    is_billable=(req_type == TransferRequest.RequestType.PATIENT_SPECIFIC),
                )
            for inv_id, qty, item_notes in consumable_items:
                inv = InventoryItem.objects.get(id=inv_id)
                TransferRequestItem.objects.create(
                    transfer_request=req,
                    inventory_item_id=inv_id,
                    quantity_requested=qty,
                    notes=item_notes,
                    unit_price=inv.selling_price,
                    is_billable=(req_type == TransferRequest.RequestType.PATIENT_SPECIFIC),
                )
            log_action(
                request.user, AuditLog.Action.CREATE,
                AuditLog.Module.NURSING if store.store_type == DepartmentStore.StoreType.WARD else AuditLog.Module.DEPT_PHARMACY,
                object_type='TransferRequest', object_id=req.pk, object_repr=req.request_number,
                description=f'{req.get_request_type_display()} {req.request_number} submitted for {store.name}'
                            + (f' — Patient: {patient}' if patient else ''),
                request=request,
            )
            messages.success(request, f'Requisition {req.request_number} submitted.')
            return redirect('transfer_request_detail', req_id=req.id)

    return render(request, 'dept_pharmacy/request_form.html', {
        'stores':      stores,
        'medications': medications,
        'consumables': consumables,
        'priorities':  TransferRequest.Priority.choices,
        'req_types':   TransferRequest.RequestType.choices,
        'doctors':     doctors,
        'today':       date.today().isoformat(),
    })


@hms_permission_required('core.view_dept_inventory')
def transfer_request_detail(request, req_id):
    req = get_object_or_404(
        TransferRequest.objects.select_related('patient', 'visit', 'requesting_doctor', 'invoice'),
        id=req_id,
    )
    items = req.items.select_related('medication', 'inventory_item', 'invoice_item')
    transfers = req.transfers.select_related('prepared_by', 'received_by').prefetch_related('items__medication', 'items__inventory_item')
    return render(request, 'dept_pharmacy/request_detail.html', {
        'req': req, 'items': items, 'transfers': transfers,
    })


@hms_permission_required('core.ward_supervisor_approve')
def transfer_request_ward_approve(request, req_id):
    """Optional pre-approval stage — only reachable for requests from a
    store with requires_ward_supervisor_approval=True. Approving moves the
    request into the normal PENDING (pharmacy/store review) queue."""
    req = get_object_or_404(TransferRequest, id=req_id, status=TransferRequest.Status.PENDING_WARD_APPROVAL)
    items = req.items.select_related('medication', 'inventory_item')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reject':
            req.status = TransferRequest.Status.REJECTED
            req.rejection_reason = request.POST.get('rejection_reason', '').strip()
            req.save()
            log_action(
                request.user, AuditLog.Action.REJECT, AuditLog.Module.NURSING,
                object_type='TransferRequest', object_id=req.pk, object_repr=req.request_number,
                description=f'Ward supervisor rejected request {req.request_number}', request=request,
            )
            messages.warning(request, f'Request {req.request_number} rejected.')
            return redirect('transfer_request_list')

        elif action == 'approve':
            req.status = TransferRequest.Status.PENDING
            req.ward_supervisor_approved_by = request.user
            req.ward_supervisor_approved_at = timezone.now()
            req.save()
            log_action(
                request.user, AuditLog.Action.APPROVE, AuditLog.Module.NURSING,
                object_type='TransferRequest', object_id=req.pk, object_repr=req.request_number,
                description=f'Ward supervisor approved request {req.request_number} — forwarded to store/pharmacy review',
                request=request,
            )
            messages.success(request, f'Request {req.request_number} approved and forwarded for store/pharmacy review.')
            return redirect('transfer_request_detail', req_id=req.id)

    return render(request, 'dept_pharmacy/request_ward_approve.html', {'req': req, 'items': items})


@hms_permission_required('core.approve_medication_transfer')
def transfer_request_approve(request, req_id):
    req = get_object_or_404(TransferRequest, id=req_id, status=TransferRequest.Status.PENDING)
    items = req.items.select_related('medication', 'inventory_item')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reject':
            req.status = TransferRequest.Status.REJECTED
            req.rejection_reason = request.POST.get('rejection_reason', '').strip()
            req.approved_by = request.user
            req.approval_date = timezone.now()
            req.save()
            messages.warning(request, f'Request {req.request_number} rejected.')
            return redirect('transfer_request_list')

        elif action == 'approve':
            errors = []
            approved_items = []
            for item in items:
                qty_key = f'approved_{item.id}'
                qty = int(request.POST.get(qty_key, item.quantity_requested) or 0)
                if qty < 0:
                    errors.append(f'Approved qty for {item.item_name} cannot be negative.')
                else:
                    approved_items.append((item, qty))

            if errors:
                for e in errors: messages.error(request, e)
            else:
                for item, qty in approved_items:
                    item.quantity_approved = qty
                    item.save()
                req.status       = TransferRequest.Status.APPROVED
                req.approved_by  = request.user
                req.approval_date = timezone.now()
                req.notes        = (req.notes + '\n' + request.POST.get('approval_notes', '')).strip()
                req.save()
                log_action(
                    request.user, AuditLog.Action.APPROVE, AuditLog.Module.DEPT_PHARMACY,
                    object_type='TransferRequest', object_id=req.pk,
                    object_repr=req.request_number,
                    description=f'Transfer request {req.request_number} approved for {req.requesting_store.name}',
                    extra_data={'store': req.requesting_store.name, 'request_number': req.request_number},
                    request=request,
                )
                messages.success(request, f'Request {req.request_number} approved. Proceed to fulfill it.')
                return redirect('transfer_request_fulfill', req_id=req.id)

    return render(request, 'dept_pharmacy/request_approve.html', {'req': req, 'items': items})


@hms_permission_required('core.approve_medication_transfer')
@db_transaction.atomic
def transfer_request_fulfill(request, req_id):
    req = get_object_or_404(TransferRequest, id=req_id, status=TransferRequest.Status.APPROVED)
    items = req.items.filter(quantity_approved__gt=0).select_related('medication', 'inventory_item')
    med_items = [i for i in items if i.medication_id]
    consumable_items = [i for i in items if i.inventory_item_id]

    # Gather available pharmacy batches for each medication line (consumable
    # lines are deducted directly from InventoryItem.quantity_in_stock — no
    # batch tracking, mirroring lab_consumable_use's pattern).
    batch_options = {}
    for item in med_items:
        batches = MedicationBatch.objects.filter(
            medication=item.medication,
            is_active=True,
            quantity_available__gt=0,
            status=MedicationBatch.BatchStatus.ACTIVE,
            expiration_date__gt=date.today(),
        ).order_by('expiration_date')
        batch_options[item.id] = list(batches)

    if request.method == 'POST':
        errors = []
        med_line_data = []          # (item, batch, qty)
        consumable_line_data = []   # (item, qty)

        for item in med_items:
            batch_id = request.POST.get(f'batch_{item.id}')
            qty = int(request.POST.get(f'fulfill_qty_{item.id}', item.quantity_approved) or 0)
            if qty <= 0:
                continue
            if not batch_id:
                errors.append(f'Select a batch for {item.item_name}.')
                continue
            batch = get_object_or_404(MedicationBatch, id=batch_id)
            if batch.quantity_available < qty:
                errors.append(f'Not enough stock for {item.item_name}: available {batch.quantity_available}, requested {qty}.')
            else:
                med_line_data.append((item, batch, qty))

        for item in consumable_items:
            qty = int(request.POST.get(f'fulfill_qty_{item.id}', item.quantity_approved) or 0)
            if qty <= 0:
                continue
            if item.inventory_item.quantity_in_stock < qty:
                errors.append(f'Not enough stock for {item.item_name}: available {item.inventory_item.quantity_in_stock}, requested {qty}.')
            else:
                consumable_line_data.append((item, qty))

        if not med_line_data and not consumable_line_data and not errors:
            errors.append('No items to fulfill.')

        if errors:
            for e in errors: messages.error(request, e)
        else:
            # Create the transfer
            transfer = DepartmentTransfer.objects.create(
                transfer_number=_txn_num(),
                transfer_type=DepartmentTransfer.TransferType.PHARM_TO_DEPT,
                dest_store=req.requesting_store,
                transfer_request=req,
                status=DepartmentTransfer.Status.PENDING,
                transfer_date=timezone.now(),
                prepared_by=request.user,
                notes=request.POST.get('notes', '').strip(),
            )

            for item, batch, qty in med_line_data:
                DepartmentTransferItem.objects.create(
                    transfer=transfer,
                    medication=item.medication,
                    source_batch=batch,
                    batch_number=batch.batch_number,
                    expiration_date=batch.expiration_date,
                    quantity_transferred=qty,
                    unit_cost=item.medication.purchase_price,
                )
                item.quantity_issued = qty
                item.save(update_fields=['quantity_issued'])
                # Deduct from pharmacy batch
                batch.quantity_available -= qty
                batch.save()
                # Record pharmacy stock transaction
                from core.views_med_inventory import _gen_tx_ref
                StockTransaction.objects.create(
                    medication=item.medication,
                    batch=batch,
                    transaction_type=StockTransaction.TxType.TRANSFER_OUT,
                    quantity_in=0,
                    quantity_out=qty,
                    balance_after=item.medication.current_stock,
                    unit_cost=item.medication.purchase_price,
                    total_value=item.medication.purchase_price * qty,
                    reference_number=_gen_tx_ref(),
                    notes=f'Transfer to {req.requesting_store.name} — {transfer.transfer_number}',
                    source_location=None,
                    dest_location=None,
                    performed_by=request.user,
                    transaction_date=timezone.now(),
                )

            for item, qty in consumable_line_data:
                DepartmentTransferItem.objects.create(
                    transfer=transfer,
                    inventory_item=item.inventory_item,
                    quantity_transferred=qty,
                    unit_cost=item.inventory_item.unit_cost,
                )
                item.quantity_issued = qty
                item.save(update_fields=['quantity_issued'])
                inv = item.inventory_item
                inv.quantity_in_stock -= qty
                inv.save(update_fields=['quantity_in_stock'])
                InventoryTransaction.objects.create(
                    inventory_item=inv,
                    transaction_type=InventoryTransaction.TxType.ISSUE,
                    quantity_out=qty,
                    balance_after=inv.quantity_in_stock,
                    unit_cost=inv.unit_cost,
                    reference_number=transfer.transfer_number,
                    notes=f'Transfer to {req.requesting_store.name} — {transfer.transfer_number}',
                    performed_by=request.user,
                )

            # Fully issued only if every approved line was issued in full —
            # this is the fix for the previously dead PARTIAL status.
            fully_issued = all(i.quantity_issued >= i.quantity_approved for i in items)
            req.status           = TransferRequest.Status.FULFILLED if fully_issued else TransferRequest.Status.PARTIAL
            req.fulfillment_date = timezone.now()
            req.save()
            log_action(
                request.user, AuditLog.Action.ISSUE, AuditLog.Module.DEPT_PHARMACY,
                object_type='TransferRequest', object_id=req.pk, object_repr=req.request_number,
                description=f'Request {req.request_number} {"fully" if fully_issued else "partially"} fulfilled via {transfer.transfer_number}',
                request=request,
            )

            # ── Patient-specific: auto-create billing invoice ────────────────
            if req.request_type == TransferRequest.RequestType.PATIENT_SPECIFIC and req.patient:
                invoice = Invoice.objects.create(
                    patient=req.patient,
                    visit=req.visit,
                    created_by=request.user,
                    status=Invoice.Status.DRAFT,
                    notes=f'Auto-generated from requisition {req.request_number}',
                )
                for item, batch, qty in med_line_data:
                    price = item.unit_price or item.medication.selling_price
                    inv_item = InvoiceItem.objects.create(
                        invoice=invoice,
                        service_type=InvoiceItem.ServiceType.MEDICATION,
                        description=item.medication.name,
                        quantity=qty,
                        unit_price=price,
                        total=price * qty,
                    )
                    item.invoice_item = inv_item
                    item.save(update_fields=['invoice_item'])
                for item, qty in consumable_line_data:
                    price = item.unit_price or item.inventory_item.unit_cost
                    inv_item = InvoiceItem.objects.create(
                        invoice=invoice,
                        service_type=InvoiceItem.ServiceType.OTHER,
                        description=item.inventory_item.name,
                        quantity=qty,
                        unit_price=price,
                        total=price * qty,
                    )
                    item.invoice_item = inv_item
                    item.save(update_fields=['invoice_item'])
                from django.db.models import Sum as _Sum
                invoice.total_amount = invoice.items.aggregate(t=_Sum('total'))['t'] or 0
                invoice.save(update_fields=['total_amount', 'updated_at'])
                req.invoice = invoice
                req.save(update_fields=['invoice'])
                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
                    object_type='Invoice', object_id=invoice.pk, object_repr=invoice.invoice_number,
                    description=f'Invoice {invoice.invoice_number} auto-created from patient requisition {req.request_number}',
                    request=request,
                )

            messages.success(request, f'Transfer {transfer.transfer_number} created. Awaiting department receipt.')
            return redirect('dept_transfer_detail', transfer_id=transfer.id)

    return render(request, 'dept_pharmacy/request_fulfill.html', {
        'req':          req,
        'items':        items,
        'batch_options': batch_options,
    })


# ── Department Transfer ───────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_inventory')
def dept_transfer_list(request):
    qs = DepartmentTransfer.objects.select_related('source_store', 'dest_store', 'prepared_by')

    tx_type  = request.GET.get('type', '')
    status   = request.GET.get('status', '')
    store_id = request.GET.get('store', '')

    if tx_type:  qs = qs.filter(transfer_type=tx_type)
    if status:   qs = qs.filter(status=status)
    if store_id:
        qs = qs.filter(Q(source_store_id=store_id) | Q(dest_store_id=store_id))

    paginator = Paginator(qs, 30)
    return render(request, 'dept_pharmacy/transfer_list.html', {
        'page_obj':  paginator.get_page(request.GET.get('page')),
        'stores':    DepartmentStore.objects.filter(is_active=True),
        'tx_types':  DepartmentTransfer.TransferType.choices,
        'statuses':  DepartmentTransfer.Status.choices,
        'tx_type':   tx_type,
        'status':    status,
        'store_id':  store_id,
    })


@hms_permission_required('core.view_dept_inventory')
def dept_transfer_detail(request, transfer_id):
    transfer = get_object_or_404(DepartmentTransfer, id=transfer_id)
    items = transfer.items.select_related('medication', 'source_batch', 'dept_batch')
    return render(request, 'dept_pharmacy/transfer_detail.html', {'transfer': transfer, 'items': items})


@hms_permission_required('core.record_dept_usage')
@db_transaction.atomic
def dept_transfer_receive(request, transfer_id):
    """Department confirms receipt — stock moves into department store."""
    transfer = get_object_or_404(DepartmentTransfer, id=transfer_id, status=DepartmentTransfer.Status.PENDING)

    if request.method == 'POST':
        items = transfer.items.select_related('medication', 'source_batch')
        for t_item in items:
            qty = int(request.POST.get(f'recv_qty_{t_item.id}', t_item.quantity_transferred) or 0)
            if qty <= 0:
                continue
            stock = _add_dept_stock(
                transfer.dest_store,
                t_item.medication,
                t_item.source_batch,
                qty,
            )
            # Link the dept_batch back onto the transfer item
            db_batch = stock.batches.filter(
                batch_number=t_item.batch_number,
            ).first()
            if db_batch:
                t_item.dept_batch = db_batch
                t_item.save()

        transfer.status        = DepartmentTransfer.Status.COMPLETED
        transfer.received_date = timezone.now()
        transfer.received_by   = request.user
        transfer.notes         = (transfer.notes + '\n' + request.POST.get('notes', '')).strip()
        transfer.save()

        messages.success(request, f'Transfer {transfer.transfer_number} received. Stock updated.')
        return redirect('dept_store_detail', store_id=transfer.dest_store_id)

    items = transfer.items.select_related('medication')
    return render(request, 'dept_pharmacy/transfer_receive.html', {'transfer': transfer, 'items': items})


@hms_permission_required('core.record_dept_usage')
@db_transaction.atomic
def dept_return_create(request, store_id):
    """Department returns medication back to main pharmacy."""
    store       = get_object_or_404(DepartmentStore, id=store_id)
    stock_items = store.stock_items.filter(quantity_available__gt=0).select_related('medication')

    if request.method == 'POST':
        p      = request.POST
        errors = []
        lines  = []

        idx = 0
        while True:
            stock_id = p.get(f'stock_{idx}')
            qty      = p.get(f'qty_{idx}')
            if stock_id is None:
                break
            if stock_id and qty:
                qty = int(qty)
                stock = get_object_or_404(DepartmentStock, id=stock_id, department_store=store)
                if qty > stock.quantity_available:
                    errors.append(f'{stock.medication.name}: return qty {qty} exceeds available {stock.quantity_available}.')
                elif qty > 0:
                    lines.append((stock, qty, p.get(f'reason_{idx}', '').strip()))
            idx += 1

        if not lines and not errors:
            errors.append('Select at least one medication to return.')

        if errors:
            for e in errors: messages.error(request, e)
        else:
            transfer = DepartmentTransfer.objects.create(
                transfer_number=_txn_num(),
                transfer_type=DepartmentTransfer.TransferType.DEPT_TO_PHARM,
                source_store=store,
                status=DepartmentTransfer.Status.COMPLETED,
                transfer_date=timezone.now(),
                received_date=timezone.now(),
                prepared_by=request.user,
                received_by=request.user,
                notes=p.get('notes', '').strip(),
            )
            for stock, qty, reason in lines:
                med    = stock.medication
                # FIFO batch
                d_batch = stock.batches.filter(is_active=True, quantity_available__gt=0).order_by('expiration_date').first()
                exp_date = d_batch.expiration_date if d_batch else (date.today() + timedelta(days=365))
                batch_num = d_batch.batch_number if d_batch else 'RETURN'

                DepartmentTransferItem.objects.create(
                    transfer=transfer,
                    medication=med,
                    batch_number=batch_num,
                    expiration_date=exp_date,
                    quantity_transferred=qty,
                    unit_cost=med.purchase_price,
                )
                # Deduct from dept stock
                _deduct_dept_stock(stock, qty, d_batch)

                # Return to pharmacy: find the source batch or create an adjustment
                pharm_batch = d_batch.source_batch if d_batch and d_batch.source_batch else None
                if pharm_batch:
                    pharm_batch.quantity_available += qty
                    pharm_batch.save()
                else:
                    # Create a new batch for the return
                    pharm_batch = MedicationBatch.objects.create(
                        medication=med,
                        batch_number=f'RTN-{transfer.transfer_number}-{med.code}',
                        expiration_date=exp_date,
                        quantity_received=qty,
                        quantity_available=qty,
                        purchase_price=med.purchase_price,
                        received_date=date.today(),
                        received_by=request.user,
                        notes=f'Return from {store.name}',
                    )

                from core.views_med_inventory import _gen_tx_ref
                StockTransaction.objects.create(
                    medication=med,
                    batch=pharm_batch,
                    transaction_type=StockTransaction.TxType.RETURN,
                    quantity_in=qty,
                    quantity_out=0,
                    balance_after=med.current_stock,
                    unit_cost=med.purchase_price,
                    total_value=med.purchase_price * qty,
                    reference_number=_gen_tx_ref(),
                    notes=f'Return from {store.name} — {transfer.transfer_number}. Reason: {reason}',
                    performed_by=request.user,
                    transaction_date=timezone.now(),
                )

            messages.success(request, f'Return {transfer.transfer_number} processed. {sum(l[1] for l in lines)} units returned to pharmacy.')
            return redirect('dept_store_detail', store_id=store.id)

    return render(request, 'dept_pharmacy/return_form.html', {'store': store, 'stock_items': stock_items})


# ── Usage recording ───────────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_inventory')
def dept_usage_list(request):
    qs = DepartmentUsage.objects.select_related('department_store', 'medication', 'responsible_staff', 'patient')

    store_id = request.GET.get('store', '')
    u_type   = request.GET.get('type', '')
    date_from = request.GET.get('from', '')
    date_to   = request.GET.get('to', '')
    q         = request.GET.get('q', '').strip()

    if store_id: qs = qs.filter(department_store_id=store_id)
    if u_type:   qs = qs.filter(usage_type=u_type)
    if date_from: qs = qs.filter(usage_date__date__gte=date_from)
    if date_to:   qs = qs.filter(usage_date__date__lte=date_to)
    if q:
        qs = qs.filter(Q(medication__brand_name__icontains=q) | Q(usage_number__icontains=q))

    paginator = Paginator(qs, 30)
    return render(request, 'dept_pharmacy/usage_list.html', {
        'page_obj':   paginator.get_page(request.GET.get('page')),
        'stores':     DepartmentStore.objects.filter(is_active=True),
        'usage_types': DepartmentUsage.UsageType.choices,
        'store_id':   store_id,
        'u_type':     u_type,
        'date_from':  date_from,
        'date_to':    date_to,
        'q':          q,
    })


@hms_permission_required('core.record_dept_usage')
@db_transaction.atomic
def dept_usage_create(request, store_id):
    store       = get_object_or_404(DepartmentStore, id=store_id)
    stock_items = store.stock_items.filter(quantity_available__gt=0).select_related('medication')
    patients    = Patient.objects.filter(is_active=True).order_by('last_name', 'first_name')

    if request.method == 'POST':
        p       = request.POST
        errors  = []
        lines   = []

        idx = 0
        while True:
            stock_id = p.get(f'stock_{idx}')
            qty      = p.get(f'qty_{idx}')
            if stock_id is None:
                break
            if stock_id and qty:
                qty = int(qty)
                stock = get_object_or_404(DepartmentStock, id=stock_id, department_store=store)
                if qty > stock.quantity_available:
                    errors.append(f'{stock.medication.name}: qty {qty} exceeds stock {stock.quantity_available}.')
                elif qty > 0:
                    lines.append((stock, qty, p.get(f'usage_type_{idx}', DepartmentUsage.UsageType.ADMINISTRATION), p.get(f'reason_{idx}', '').strip()))
            idx += 1

        patient_id   = p.get('patient') or None
        visit_id     = p.get('visit') or None
        usage_date   = p.get('usage_date') or timezone.now().isoformat()[:16]
        notes        = p.get('notes', '').strip()

        if not lines and not errors:
            errors.append('Add at least one medication usage line.')

        if errors:
            for e in errors: messages.error(request, e)
        else:
            from django.utils.dateparse import parse_datetime
            try:
                dt = parse_datetime(usage_date + ':00') or timezone.now()
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
            except Exception:
                dt = timezone.now()

            for stock, qty, u_type, reason in lines:
                d_batch = stock.batches.filter(is_active=True, quantity_available__gt=0).order_by('expiration_date').first()
                usage = DepartmentUsage.objects.create(
                    usage_number=_usage_num(),
                    department_store=store,
                    medication=stock.medication,
                    dept_batch=d_batch,
                    quantity_used=qty,
                    usage_type=u_type,
                    patient_id=patient_id,
                    visit_id=visit_id,
                    responsible_staff=request.user,
                    usage_date=dt,
                    reason=reason,
                    notes=notes,
                )
                _deduct_dept_stock(stock, qty, d_batch)

            med_names = ', '.join(s.medication.name for s, *_ in lines)
            log_action(
                request.user, AuditLog.Action.DISPENSE, AuditLog.Module.DEPT_PHARMACY,
                object_type='DepartmentUsage', object_id=store.pk,
                object_repr=store.name,
                description=f'{len(lines)} usage record(s) saved at {store.name}: {med_names}',
                extra_data={'store': store.name, 'lines': len(lines),
                            'medications': med_names},
                request=request,
            )
            messages.success(request, f'{len(lines)} usage record(s) saved.')
            return redirect('dept_store_detail', store_id=store.id)

    return render(request, 'dept_pharmacy/usage_form.html', {
        'store':       store,
        'stock_items': stock_items,
        'patients':    patients,
        'usage_types': DepartmentUsage.UsageType.choices,
        'now':         timezone.now().strftime('%Y-%m-%dT%H:%M'),
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_dept_reports')
def report_dept_stock_on_hand(request):
    stores   = DepartmentStore.objects.filter(is_active=True)
    store_id = request.GET.get('store', '')
    q        = request.GET.get('q', '').strip()

    store = None
    stock_items = []
    if store_id:
        store = get_object_or_404(DepartmentStore, id=store_id)
        stock_items = list(store.stock_items.select_related('medication', 'medication__inventory_item__category').order_by('medication__brand_name'))
        if q:
            stock_items = [s for s in stock_items if q.lower() in s.medication.name.lower() or q.lower() in s.medication.generic_name.lower()]

    if request.GET.get('export') == 'csv' and store:
        return _export_dept_stock_csv(store, stock_items)

    return render(request, 'dept_pharmacy/reports/stock_on_hand.html', {
        'stores':      stores,
        'store':       store,
        'stock_items': stock_items,
        'q':           q,
        'store_id':    store_id,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.view_dept_reports')
def report_dept_stock_card(request):
    stores      = DepartmentStore.objects.filter(is_active=True)
    store_id    = request.GET.get('store', '')
    med_id      = request.GET.get('med', '')
    today       = date.today()
    date_from   = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to     = request.GET.get('to', today.isoformat())

    store = get_object_or_404(DepartmentStore, id=store_id) if store_id else None
    med   = get_object_or_404(Medication, id=med_id) if med_id else None

    medications = []
    if store:
        medications = Medication.objects.filter(
            dept_stocks__department_store=store
        ).distinct().order_by('brand_name')

    transactions = []
    if store and med:
        usages = DepartmentUsage.objects.filter(
            department_store=store, medication=med,
            usage_date__date__gte=date_from,
            usage_date__date__lte=date_to,
        ).order_by('usage_date')
        transfers = DepartmentTransferItem.objects.filter(
            transfer__dest_store=store,
            medication=med,
            transfer__status=DepartmentTransfer.Status.COMPLETED,
            transfer__received_date__date__gte=date_from,
            transfer__received_date__date__lte=date_to,
        ).select_related('transfer').order_by('transfer__received_date')

        # Merge and sort
        events = []
        for t in transfers:
            events.append({'date': t.transfer.received_date, 'type': 'receive', 'qty_in': t.quantity_transferred, 'qty_out': 0, 'ref': t.transfer.transfer_number, 'by': t.transfer.received_by})
        for u in usages:
            events.append({'date': u.usage_date, 'type': 'usage', 'qty_in': 0, 'qty_out': u.quantity_used, 'ref': u.usage_number, 'by': u.responsible_staff, 'usage_type': u.get_usage_type_display(), 'reason': u.reason})
        transactions = sorted(events, key=lambda x: x['date'] or timezone.now())

    return render(request, 'dept_pharmacy/reports/stock_card.html', {
        'stores':       stores,
        'store':        store,
        'med':          med,
        'medications':  medications,
        'transactions': transactions,
        'date_from':    date_from,
        'date_to':      date_to,
        'generated_at': timezone.now(),
    })


@hms_permission_required('core.view_dept_reports')
def report_dept_consumption(request):
    stores    = DepartmentStore.objects.filter(is_active=True)
    today     = date.today()
    store_id  = request.GET.get('store', '')
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())
    u_type    = request.GET.get('type', '')

    qs = DepartmentUsage.objects.select_related('department_store', 'medication', 'responsible_staff', 'patient').order_by('-usage_date')

    store = None
    if store_id:
        store = get_object_or_404(DepartmentStore, id=store_id)
        qs = qs.filter(department_store=store)
    if date_from: qs = qs.filter(usage_date__date__gte=date_from)
    if date_to:   qs = qs.filter(usage_date__date__lte=date_to)
    if u_type:    qs = qs.filter(usage_type=u_type)

    usages = list(qs[:500])
    total_units = sum(u.quantity_used for u in usages)

    if request.GET.get('export') == 'csv':
        return _export_consumption_csv(usages, store, date_from, date_to)

    return render(request, 'dept_pharmacy/reports/consumption.html', {
        'stores':      stores,
        'store':       store,
        'usages':      usages,
        'total_units': total_units,
        'usage_types': DepartmentUsage.UsageType.choices,
        'date_from':   date_from,
        'date_to':     date_to,
        'u_type':      u_type,
        'store_id':    store_id,
        'generated_at': timezone.now(),
    })


@hms_permission_required('core.view_dept_reports')
def report_transfer_history(request):
    stores    = DepartmentStore.objects.filter(is_active=True)
    today     = date.today()
    store_id  = request.GET.get('store', '')
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())
    tx_type   = request.GET.get('type', '')
    status    = request.GET.get('status', '')

    qs = DepartmentTransfer.objects.select_related(
        'source_store', 'dest_store', 'prepared_by', 'received_by', 'transfer_request',
    ).prefetch_related('items__medication').order_by('-created_at')

    if store_id: qs = qs.filter(Q(source_store_id=store_id) | Q(dest_store_id=store_id))
    if tx_type:  qs = qs.filter(transfer_type=tx_type)
    if status:   qs = qs.filter(status=status)
    if date_from: qs = qs.filter(created_at__date__gte=date_from)
    if date_to:   qs = qs.filter(created_at__date__lte=date_to)

    transfers = list(qs[:500])

    if request.GET.get('export') == 'csv':
        return _export_transfer_csv(transfers)

    return render(request, 'dept_pharmacy/reports/transfer_history.html', {
        'stores':    stores,
        'transfers': transfers,
        'tx_types':  DepartmentTransfer.TransferType.choices,
        'statuses':  DepartmentTransfer.Status.choices,
        'store_id':  store_id,
        'date_from': date_from,
        'date_to':   date_to,
        'tx_type':   tx_type,
        'status':    status,
        'generated_at': timezone.now(),
    })


# ── CSV export helpers ────────────────────────────────────────────────────────

def _export_dept_stock_csv(store, stock_items):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="dept_stock_{store.name}_{date.today()}.csv"'
    w = csv.writer(resp)
    w.writerow([f'Department Stock on Hand — {store.name}'])
    w.writerow(['Medication', 'Generic Name', 'Category', 'Stock Qty', 'Unit', 'Status', 'Min Qty'])
    for s in stock_items:
        w.writerow([
            s.medication.name, s.medication.generic_name,
            s.medication.category.name if s.medication.category else '',
            s.quantity_available, s.medication.unit_of_measure,
            s.stock_status, s.minimum_quantity,
        ])
    return resp


def _export_consumption_csv(usages, store, date_from, date_to):
    resp = HttpResponse(content_type='text/csv')
    name = store.name if store else 'All Departments'
    resp['Content-Disposition'] = f'attachment; filename="consumption_{date.today()}.csv"'
    w = csv.writer(resp)
    w.writerow([f'Department Consumption Report — {name} — {date_from} to {date_to}'])
    w.writerow(['Date', 'Department', 'Item', 'Generic Name', 'Qty Used', 'Type', 'Patient', 'Staff', 'Reason'])
    for u in usages:
        item_name = u.medication.name if u.medication_id else u.inventory_item.name
        generic_name = u.medication.generic_name if u.medication_id else ''
        w.writerow([
            u.usage_date.strftime('%Y-%m-%d %H:%M'),
            u.department_store.name, item_name, generic_name,
            u.quantity_used, u.get_usage_type_display(),
            str(u.patient) if u.patient else '',
            u.responsible_staff.get_full_name() or u.responsible_staff.username if u.responsible_staff else '',
            u.reason,
        ])
    return resp


def _export_transfer_csv(transfers):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="transfers_{date.today()}.csv"'
    w = csv.writer(resp)
    w.writerow(['Transfer No.', 'Type', 'Source', 'Destination', 'Status', 'Transfer Date', 'Received Date', 'Prepared By', 'Medications'])
    for t in transfers:
        meds = '; '.join(f"{i.medication.name} x{i.quantity_transferred}" for i in t.items.all())
        w.writerow([
            t.transfer_number, t.get_transfer_type_display(),
            t.source_store.name if t.source_store else 'Main Pharmacy',
            t.dest_store.name if t.dest_store else 'Main Pharmacy',
            t.get_status_display(),
            t.transfer_date.strftime('%Y-%m-%d %H:%M') if t.transfer_date else '',
            t.received_date.strftime('%Y-%m-%d %H:%M') if t.received_date else '',
            t.prepared_by.get_full_name() or t.prepared_by.username if t.prepared_by else '',
            meds,
        ])
    return resp


# ── Patient Search API ────────────────────────────────────────────────────────

def patient_search_api(request):
    from django.http import JsonResponse
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    patients = Patient.objects.filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(middle_name__icontains=q) | Q(card_number__icontains=q)
    ).order_by('first_name')[:10]
    return JsonResponse({'results': [
        {'id': p.id,
         'text': f"{p.first_name} {p.middle_name} {p.last_name}".replace('  ', ' ').strip(),
         'card': p.card_number, 'mobile': p.mobile}
        for p in patients
    ]})


def patient_visits_api(request, patient_id):
    from django.http import JsonResponse
    visits = Visit.objects.filter(
        patient_id=patient_id,
    ).exclude(status=Visit.Status.DISCHARGED).select_related('department').order_by('-created_at')[:10]
    return JsonResponse({'visits': [
        {'id': v.id,
         'text': f"Visit #{v.id} — {v.department.name if v.department_id else ''} ({v.get_status_display()})"}
        for v in visits
    ]})


# ── Patient Requisition Reports ───────────────────────────────────────────────

@hms_permission_required('core.view_patient_requisitions')
def report_patient_requisitions(request):
    today     = date.today()
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())
    store_id  = request.GET.get('store', '')
    status    = request.GET.get('status', '')
    priority  = request.GET.get('priority', '')
    patient_q = request.GET.get('patient', '').strip()

    qs = (TransferRequest.objects
          .filter(request_type=TransferRequest.RequestType.PATIENT_SPECIFIC)
          .select_related('requesting_store', 'patient', 'visit', 'requested_by',
                          'approved_by', 'requesting_doctor', 'invoice')
          .prefetch_related('items__medication', 'items__inventory_item')
          .order_by('-request_date'))

    if date_from: qs = qs.filter(request_date__date__gte=date_from)
    if date_to:   qs = qs.filter(request_date__date__lte=date_to)
    if store_id:  qs = qs.filter(requesting_store_id=store_id)
    if status:    qs = qs.filter(status=status)
    if priority:  qs = qs.filter(priority=priority)
    if patient_q:
        qs = qs.filter(
            Q(patient__first_name__icontains=patient_q) |
            Q(patient__last_name__icontains=patient_q) |
            Q(patient__card_number__icontains=patient_q)
        )

    reqs = list(qs[:500])
    for r in reqs:
        r.calc_item_count = r.items.count()
        r.calc_total_value = sum(
            (i.unit_price or 0) * (i.quantity_issued or i.quantity_approved or i.quantity_requested)
            for i in r.items.all()
        )

    if request.GET.get('export') == 'csv':
        return _export_patient_requisitions_csv(reqs, date_from, date_to)

    paginator = Paginator(reqs, 30)
    return render(request, 'dept_pharmacy/reports/patient_requisitions.html', {
        'page_obj':   paginator.get_page(request.GET.get('page')),
        'stores':     DepartmentStore.objects.filter(is_active=True),
        'statuses':   TransferRequest.Status.choices,
        'priorities': TransferRequest.Priority.choices,
        'store_id':   store_id,
        'status':     status,
        'priority':   priority,
        'patient_q':  patient_q,
        'date_from':  date_from,
        'date_to':    date_to,
        'total_reqs': len(reqs),
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.view_patient_requisitions')
def report_outstanding_billing(request):
    today     = date.today()
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())
    store_id  = request.GET.get('store', '')

    qs = (TransferRequest.objects
          .filter(request_type=TransferRequest.RequestType.PATIENT_SPECIFIC,
                  invoice__isnull=False)
          .exclude(invoice__status__in=[Invoice.Status.PAID, Invoice.Status.WAIVED,
                                        Invoice.Status.CANCELLED])
          .select_related('requesting_store', 'patient', 'visit', 'invoice', 'requested_by')
          .order_by('-request_date'))

    if date_from: qs = qs.filter(request_date__date__gte=date_from)
    if date_to:   qs = qs.filter(request_date__date__lte=date_to)
    if store_id:  qs = qs.filter(requesting_store_id=store_id)

    reqs = list(qs[:500])
    total_outstanding = sum(r.invoice.balance for r in reqs if r.invoice)

    return render(request, 'dept_pharmacy/reports/outstanding_billing.html', {
        'reqs':              reqs,
        'stores':            DepartmentStore.objects.filter(is_active=True),
        'total_outstanding': total_outstanding,
        'store_id':          store_id,
        'date_from':         date_from,
        'date_to':           date_to,
        'generated_at':      timezone.now(),
        'generated_by':      request.user,
    })


@hms_permission_required('core.view_dept_reports')
def report_dept_requisitions(request):
    today     = date.today()
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())
    store_id  = request.GET.get('store', '')
    status    = request.GET.get('status', '')
    req_type  = request.GET.get('req_type', '')

    qs = (TransferRequest.objects
          .select_related('requesting_store', 'patient', 'requested_by', 'approved_by', 'invoice')
          .prefetch_related('items')
          .order_by('-request_date'))

    if date_from: qs = qs.filter(request_date__date__gte=date_from)
    if date_to:   qs = qs.filter(request_date__date__lte=date_to)
    if store_id:  qs = qs.filter(requesting_store_id=store_id)
    if status:    qs = qs.filter(status=status)
    if req_type:  qs = qs.filter(request_type=req_type)

    reqs = list(qs[:500])
    for r in reqs:
        r.calc_item_count = r.items.count()

    if request.GET.get('export') == 'csv':
        return _export_dept_requisitions_csv(reqs, date_from, date_to)

    paginator = Paginator(reqs, 30)
    return render(request, 'dept_pharmacy/reports/dept_requisitions.html', {
        'page_obj':   paginator.get_page(request.GET.get('page')),
        'stores':     DepartmentStore.objects.filter(is_active=True),
        'statuses':   TransferRequest.Status.choices,
        'req_types':  TransferRequest.RequestType.choices,
        'store_id':   store_id,
        'status':     status,
        'req_type':   req_type,
        'date_from':  date_from,
        'date_to':    date_to,
        'total_reqs': len(reqs),
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


def _export_patient_requisitions_csv(reqs, date_from, date_to):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="patient_requisitions_{date.today()}.csv"'
    w = csv.writer(resp)
    w.writerow([f'Patient-Specific Requisitions — {date_from} to {date_to}'])
    w.writerow([])
    w.writerow(['Req #', 'Date', 'Department', 'Patient', 'MRN', 'Visit', 'Priority',
                'Status', 'Requesting Doctor', 'Clinical Reason',
                'Items', 'Invoice #', 'Invoice Status', 'Total Value (ETB)', 'Requested By'])
    for r in reqs:
        items_str = '; '.join(f"{i.item_name} x{i.quantity_requested}" for i in r.items.all())
        w.writerow([
            r.request_number, r.request_date.strftime('%Y-%m-%d %H:%M'),
            r.requesting_store.name,
            f"{r.patient.first_name} {r.patient.last_name}" if r.patient else '',
            r.patient.card_number if r.patient else '',
            str(r.visit.id) if r.visit else '',
            r.get_priority_display(), r.get_status_display(),
            r.requesting_doctor.get_full_name() if r.requesting_doctor else '',
            r.clinical_reason, items_str,
            r.invoice.invoice_number if r.invoice else '',
            r.invoice.status if r.invoice else '',
            float(getattr(r, 'calc_total_value', 0)),
            r.requested_by.get_full_name() or r.requested_by.username if r.requested_by else '',
        ])
    return resp


def _export_dept_requisitions_csv(reqs, date_from, date_to):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="dept_requisitions_{date.today()}.csv"'
    w = csv.writer(resp)
    w.writerow([f'Department Requisitions — {date_from} to {date_to}'])
    w.writerow([])
    w.writerow(['Req #', 'Date', 'Type', 'Department', 'Patient', 'Priority',
                'Status', 'Items', 'Invoice #', 'Requested By'])
    for r in reqs:
        items_str = '; '.join(f"{i.item_name} x{i.quantity_requested}" for i in r.items.all())
        w.writerow([
            r.request_number, r.request_date.strftime('%Y-%m-%d %H:%M'),
            r.get_request_type_display(), r.requesting_store.name,
            f"{r.patient.first_name} {r.patient.last_name}" if r.patient else '—',
            r.get_priority_display(), r.get_status_display(),
            items_str,
            r.invoice.invoice_number if r.invoice else '',
            r.requested_by.get_full_name() or r.requested_by.username if r.requested_by else '',
        ])
    return resp
