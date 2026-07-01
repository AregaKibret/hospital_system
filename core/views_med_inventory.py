"""Medication Inventory Management — views."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_action, build_changes
from .decorators import hms_permission_required
from .models import (
    AuditLog, MedicationBatch, MedicationCategory, Medication,
    Patient, StockTransaction, StorageLocation, Supplier,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _alert_counts():
    today = date.today()
    d30   = today + timedelta(days=30)
    d90   = today + timedelta(days=90)

    low_stock = Medication.objects.filter(is_active=True).count()  # computed per-object
    # Fast path: pull quantities in one query
    from django.db.models import OuterRef, Subquery
    low_stock = sum(
        1 for m in Medication.objects.filter(is_active=True)
        if m.is_low_stock
    )
    out_of_stock = sum(
        1 for m in Medication.objects.filter(is_active=True)
        if m.is_out_of_stock
    )
    near_expiry = MedicationBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__gt=today, expiration_date__lte=d30,
    ).count()
    expired = MedicationBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__lt=today,
    ).count()
    return {
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
        'near_expiry': near_expiry,
        'expired': expired,
    }


def _gen_tx_ref():
    last = StockTransaction.objects.order_by('-id').first()
    n = (last.id + 1) if last else 1
    return f"TXN-{n:07d}"


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def med_inventory_dashboard(request):
    today = date.today()
    d30   = today + timedelta(days=30)
    d90   = today + timedelta(days=90)

    total_meds      = Medication.objects.filter(is_active=True).count()
    total_suppliers = Supplier.objects.filter(is_active=True).count()
    total_batches   = MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0).count()

    medications     = list(Medication.objects.filter(is_active=True).select_related('category', 'supplier'))
    low_stock       = [m for m in medications if m.is_low_stock]
    out_of_stock    = [m for m in medications if m.is_out_of_stock]

    near_expiry = MedicationBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__gt=today, expiration_date__lte=d30,
    ).select_related('medication').order_by('expiration_date')[:10]

    expired_batches = MedicationBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
        expiration_date__lt=today,
    ).select_related('medication').order_by('expiration_date')[:10]

    recent_tx = StockTransaction.objects.select_related(
        'medication', 'performed_by',
    ).order_by('-transaction_date')[:10]

    return render(request, 'med_inventory/dashboard.html', {
        'total_meds':      total_meds,
        'total_suppliers': total_suppliers,
        'total_batches':   total_batches,
        'low_stock_count':     len(low_stock),
        'out_of_stock_count':  len(out_of_stock),
        'near_expiry_count':   near_expiry.count() if hasattr(near_expiry, 'count') else len(near_expiry),
        'expired_count':       expired_batches.count() if hasattr(expired_batches, 'count') else len(expired_batches),
        'low_stock':       low_stock[:8],
        'near_expiry':     near_expiry,
        'expired_batches': expired_batches,
        'recent_tx':       recent_tx,
        'today':           today,
    })


# ── Medication CRUD ──────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def medication_list(request):
    qs = Medication.objects.filter(is_active=True).select_related('category', 'supplier')

    q        = request.GET.get('q', '').strip()
    category = request.GET.get('category', '')
    status   = request.GET.get('status', '')
    drug_type = request.GET.get('drug_type', '')

    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(generic_name__icontains=q)
            | Q(code__icontains=q) | Q(barcode__icontains=q)
        )
    if category:
        qs = qs.filter(category_id=category)
    if drug_type:
        qs = qs.filter(drug_type=drug_type)

    medications = list(qs)
    if status == 'low_stock':
        medications = [m for m in medications if m.is_low_stock]
    elif status == 'out_of_stock':
        medications = [m for m in medications if m.is_out_of_stock]
    elif status == 'near_expiry':
        today = date.today()
        near_ids = MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__gt=today,
            expiration_date__lte=today + timedelta(days=90),
        ).values_list('medication_id', flat=True)
        medications = [m for m in medications if m.id in set(near_ids)]

    paginator = Paginator(medications, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'med_inventory/medication_list.html', {
        'page_obj':   page_obj,
        'categories': MedicationCategory.objects.order_by('name'),
        'drug_types': Medication.DrugType.choices,
        'q':          q,
        'category':   category,
        'status':     status,
        'drug_type':  drug_type,
        'alerts':     _alert_counts(),
    })


@hms_permission_required('core.manage_medication')
def medication_create(request):
    categories = MedicationCategory.objects.order_by('name')
    suppliers  = Supplier.objects.filter(is_active=True).order_by('name')
    locations  = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []
        name = p.get('name', '').strip()
        generic_name = p.get('generic_name', '').strip()
        code = p.get('code', '').strip()
        strength = p.get('strength', '').strip()

        if not name:        errors.append('Medication name is required.')
        if not generic_name: errors.append('Generic name is required.')
        if not code:        errors.append('Medication code is required.')
        if not strength:    errors.append('Strength is required.')
        if Medication.objects.filter(code=code).exists():
            errors.append(f'Code "{code}" already exists.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            med = Medication.objects.create(
                name=name,
                generic_name=generic_name,
                scientific_name=p.get('scientific_name', '').strip(),
                code=code,
                barcode=p.get('barcode', '').strip(),
                category_id=p.get('category') or None,
                therapeutic_class=p.get('therapeutic_class', '').strip(),
                drug_type=p.get('drug_type', Medication.DrugType.TABLET),
                strength=strength,
                dosage_form=p.get('dosage_form', '').strip(),
                route=p.get('route', Medication.Route.ORAL),
                manufacturer=p.get('manufacturer', '').strip(),
                country_of_origin=p.get('country_of_origin', '').strip(),
                supplier_id=p.get('supplier') or None,
                unit_of_measure=p.get('unit_of_measure', 'Tablet').strip(),
                pack_description=p.get('pack_description', '').strip(),
                units_per_pack=int(p.get('units_per_pack') or 1),
                purchase_unit=p.get('purchase_unit', 'Box').strip(),
                dispensing_unit=p.get('dispensing_unit', 'Tablet').strip(),
                conversion_factor=int(p.get('conversion_factor') or 1),
                purchase_price=Decimal(p.get('purchase_price') or '0'),
                selling_price=Decimal(p.get('selling_price') or '0'),
                wholesale_price=Decimal(p.get('wholesale_price')) if p.get('wholesale_price') else None,
                insurance_price=Decimal(p.get('insurance_price')) if p.get('insurance_price') else None,
                minimum_stock=int(p.get('minimum_stock') or 0),
                maximum_stock=int(p.get('maximum_stock') or 0),
                reorder_level=int(p.get('reorder_level') or 0),
                reorder_quantity=int(p.get('reorder_quantity') or 0),
                safety_stock=int(p.get('safety_stock') or 0),
                registration_number=p.get('registration_number', '').strip(),
                regulatory_approval=p.get('regulatory_approval', '').strip(),
                controlled_substance=bool(p.get('controlled_substance')),
                prescription_required=bool(p.get('prescription_required', True)),
                location_id=p.get('location') or None,
                storage_condition=p.get('storage_condition', Medication.StorageCondition.ROOM_TEMP),
                notes=p.get('notes', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.MED_INVENTORY,
                object_type='Medication', object_id=med.pk, object_repr=str(med),
                description=f'Medication "{med.name}" ({med.code}) created',
                extra_data={'code': med.code, 'strength': med.strength,
                            'selling_price': str(med.selling_price)},
                request=request,
            )
            messages.success(request, f'Medication "{med.name}" created successfully.')
            return redirect('medication_detail', med_id=med.id)

    return render(request, 'med_inventory/medication_form.html', {
        'action':     'Create',
        'categories': categories,
        'suppliers':  suppliers,
        'locations':  locations,
        'drug_types': Medication.DrugType.choices,
        'routes':     Medication.Route.choices,
        'conditions': Medication.StorageCondition.choices,
        'post':       request.POST,
    })


@hms_permission_required('core.manage_medication')
def medication_edit(request, med_id):
    med        = get_object_or_404(Medication, id=med_id)
    categories = MedicationCategory.objects.order_by('name')
    suppliers  = Supplier.objects.filter(is_active=True).order_by('name')
    locations  = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []
        name = p.get('name', '').strip()
        code = p.get('code', '').strip()

        if not name: errors.append('Medication name is required.')
        if not code: errors.append('Medication code is required.')
        if Medication.objects.filter(code=code).exclude(id=med_id).exists():
            errors.append(f'Code "{code}" is already used by another medication.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # Capture old values for audit trail before mutating
            _track_fields = ['name', 'generic_name', 'strength', 'selling_price',
                             'purchase_price', 'minimum_stock', 'reorder_level']
            old_med = Medication.objects.get(pk=med_id)
            med.name               = name
            med.generic_name       = p.get('generic_name', '').strip()
            med.scientific_name    = p.get('scientific_name', '').strip()
            med.code               = code
            med.barcode            = p.get('barcode', '').strip()
            med.category_id        = p.get('category') or None
            med.therapeutic_class  = p.get('therapeutic_class', '').strip()
            med.drug_type          = p.get('drug_type', med.drug_type)
            med.strength           = p.get('strength', '').strip()
            med.dosage_form        = p.get('dosage_form', '').strip()
            med.route              = p.get('route', med.route)
            med.manufacturer       = p.get('manufacturer', '').strip()
            med.country_of_origin  = p.get('country_of_origin', '').strip()
            med.supplier_id        = p.get('supplier') or None
            med.unit_of_measure    = p.get('unit_of_measure', 'Tablet').strip()
            med.pack_description   = p.get('pack_description', '').strip()
            med.units_per_pack     = int(p.get('units_per_pack') or 1)
            med.purchase_unit      = p.get('purchase_unit', 'Box').strip()
            med.dispensing_unit    = p.get('dispensing_unit', 'Tablet').strip()
            med.conversion_factor  = int(p.get('conversion_factor') or 1)
            med.purchase_price     = Decimal(p.get('purchase_price') or '0')
            med.selling_price      = Decimal(p.get('selling_price') or '0')
            med.wholesale_price    = Decimal(p.get('wholesale_price')) if p.get('wholesale_price') else None
            med.insurance_price    = Decimal(p.get('insurance_price')) if p.get('insurance_price') else None
            med.minimum_stock      = int(p.get('minimum_stock') or 0)
            med.maximum_stock      = int(p.get('maximum_stock') or 0)
            med.reorder_level      = int(p.get('reorder_level') or 0)
            med.reorder_quantity   = int(p.get('reorder_quantity') or 0)
            med.safety_stock       = int(p.get('safety_stock') or 0)
            med.registration_number = p.get('registration_number', '').strip()
            med.regulatory_approval = p.get('regulatory_approval', '').strip()
            med.controlled_substance = bool(p.get('controlled_substance'))
            med.prescription_required = bool(p.get('prescription_required', True))
            med.location_id        = p.get('location') or None
            med.storage_condition  = p.get('storage_condition', med.storage_condition)
            med.notes              = p.get('notes', '').strip()
            med.save()
            changes = build_changes(old_med, med, _track_fields)
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.MED_INVENTORY,
                object_type='Medication', object_id=med.pk, object_repr=str(med),
                description=f'Medication "{med.name}" ({med.code}) updated',
                changes=changes,
                request=request,
            )
            messages.success(request, f'Medication "{med.name}" updated.')
            return redirect('medication_detail', med_id=med.id)

    return render(request, 'med_inventory/medication_form.html', {
        'action':     'Edit',
        'med':        med,
        'categories': categories,
        'suppliers':  suppliers,
        'locations':  locations,
        'drug_types': Medication.DrugType.choices,
        'routes':     Medication.Route.choices,
        'conditions': Medication.StorageCondition.choices,
    })


@hms_permission_required('core.read_medication_inventory')
def medication_detail(request, med_id):
    med = get_object_or_404(Medication, id=med_id)
    batches  = med.batches.filter(is_active=True).select_related('supplier', 'location').order_by('expiration_date')
    transactions = med.transactions.select_related('performed_by', 'batch', 'patient').order_by('-transaction_date')[:30]

    return render(request, 'med_inventory/medication_detail.html', {
        'med':          med,
        'batches':      batches,
        'transactions': transactions,
        'today':        date.today(),
    })


@hms_permission_required('core.manage_medication')
def medication_deactivate(request, med_id):
    if request.method == 'POST':
        med = get_object_or_404(Medication, id=med_id)
        med.is_active = False
        med.save()
        log_action(
            request.user, AuditLog.Action.DEACTIVATE, AuditLog.Module.MED_INVENTORY,
            object_type='Medication', object_id=med.pk, object_repr=str(med),
            description=f'Medication "{med.name}" ({med.code}) deactivated',
            request=request,
        )
        messages.success(request, f'Medication "{med.name}" deactivated.')
    return redirect('medication_list')


# ── Batch management ──────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def batch_list(request):
    today = date.today()
    d30   = today + timedelta(days=30)
    d90   = today + timedelta(days=90)

    qs = MedicationBatch.objects.filter(is_active=True).select_related('medication', 'supplier', 'location')

    expiry_filter = request.GET.get('expiry', '')
    q             = request.GET.get('q', '').strip()
    med_id        = request.GET.get('med', '')

    if q:
        qs = qs.filter(
            Q(batch_number__icontains=q)
            | Q(medication__name__icontains=q)
            | Q(medication__generic_name__icontains=q)
        )
    if med_id:
        qs = qs.filter(medication_id=med_id)
    if expiry_filter == 'expired':
        qs = qs.filter(expiration_date__lt=today)
    elif expiry_filter == '30':
        qs = qs.filter(expiration_date__gte=today, expiration_date__lte=d30)
    elif expiry_filter == '60':
        qs = qs.filter(expiration_date__gte=today, expiration_date__lte=today + timedelta(days=60))
    elif expiry_filter == '90':
        qs = qs.filter(expiration_date__gte=today, expiration_date__lte=d90)
    elif expiry_filter == 'valid':
        qs = qs.filter(expiration_date__gt=d90)

    qs = qs.filter(quantity_available__gt=0)
    paginator = Paginator(qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'med_inventory/batch_list.html', {
        'page_obj':     page_obj,
        'q':            q,
        'expiry_filter': expiry_filter,
        'med_id':       med_id,
        'today':        today,
        'alerts':       _alert_counts(),
    })


@hms_permission_required('core.manage_medication')
def batch_receive_select(request):
    """Landing page to pick which medication to receive stock for."""
    medications = Medication.objects.filter(is_active=True).select_related('category').order_by('name')
    q = request.GET.get('q', '').strip()
    if q:
        medications = medications.filter(
            Q(name__icontains=q) | Q(generic_name__icontains=q) | Q(code__icontains=q)
        )
    return render(request, 'med_inventory/batch_receive_select.html', {'medications': medications, 'q': q})


@hms_permission_required('core.manage_medication')
def batch_receive(request, med_id):
    med       = get_object_or_404(Medication, id=med_id, is_active=True)
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    locations = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []
        batch_number  = p.get('batch_number', '').strip()
        expiry_str    = p.get('expiration_date', '').strip()
        qty_str       = p.get('quantity', '').strip()

        if not batch_number:  errors.append('Batch number is required.')
        if not expiry_str:    errors.append('Expiration date is required.')
        if not qty_str:       errors.append('Quantity is required.')

        if not errors:
            from datetime import datetime
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            if expiry_date <= date.today():
                errors.append('Expiration date must be in the future.')
            if MedicationBatch.objects.filter(medication=med, batch_number=batch_number).exists():
                errors.append(f'Batch number "{batch_number}" already exists for this medication.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            qty       = int(qty_str)
            unit_cost = Decimal(p.get('purchase_price') or str(med.purchase_price))
            rec_date  = p.get('received_date') or date.today().isoformat()
            from datetime import datetime
            rec_date  = datetime.strptime(rec_date, '%Y-%m-%d').date()

            batch = MedicationBatch.objects.create(
                medication=med,
                batch_number=batch_number,
                lot_number=p.get('lot_number', '').strip(),
                manufacturing_date=p.get('manufacturing_date') or None,
                expiration_date=expiry_date,
                quantity_received=qty,
                quantity_available=qty,
                purchase_price=unit_cost,
                supplier_id=p.get('supplier') or None,
                location_id=p.get('location') or None,
                received_date=rec_date,
                received_by=request.user,
                purchase_order_ref=p.get('po_ref', '').strip(),
                invoice_number=p.get('invoice_number', '').strip(),
                notes=p.get('notes', '').strip(),
            )

            # Record stock transaction
            balance = med.current_stock  # now includes the new batch
            StockTransaction.objects.create(
                medication=med,
                batch=batch,
                transaction_type=StockTransaction.TxType.PURCHASE,
                quantity_in=qty,
                quantity_out=0,
                balance_after=balance,
                unit_cost=unit_cost,
                total_value=unit_cost * qty,
                reference_number=_gen_tx_ref(),
                notes=p.get('notes', '').strip(),
                performed_by=request.user,
                transaction_date=timezone.now(),
            )

            log_action(
                request.user, AuditLog.Action.RECEIVE, AuditLog.Module.MED_INVENTORY,
                object_type='MedicationBatch', object_id=batch.pk,
                object_repr=f'{med.name} / {batch_number}',
                description=f'Received {qty} units of {med.name} — batch {batch_number}, expires {expiry_date}',
                changes={'quantity': {'old': '0', 'new': str(qty)}},
                extra_data={
                    'medication': med.name,
                    'batch_number': batch_number,
                    'quantity': qty,
                    'unit_cost': str(unit_cost),
                    'expiration_date': str(expiry_date),
                    'supplier_id': str(p.get('supplier') or ''),
                },
                request=request,
            )
            messages.success(request, f'{qty} units of batch {batch_number} received for {med.name}.')
            return redirect('medication_detail', med_id=med.id)

    return render(request, 'med_inventory/batch_form.html', {
        'med':       med,
        'suppliers': suppliers,
        'locations': locations,
        'today':     date.today().isoformat(),
    })


@hms_permission_required('core.manage_medication')
def batch_dispose(request, batch_id):
    batch = get_object_or_404(MedicationBatch, id=batch_id, is_active=True)
    if request.method == 'POST':
        dispose_type = request.POST.get('dispose_type', 'expired_disposal')
        qty          = batch.quantity_available
        notes        = request.POST.get('notes', '').strip()

        tx_type = (StockTransaction.TxType.EXPIRED_DISPOSAL
                   if dispose_type == 'expired_disposal'
                   else StockTransaction.TxType.DAMAGE)

        StockTransaction.objects.create(
            medication=batch.medication,
            batch=batch,
            transaction_type=tx_type,
            quantity_in=0,
            quantity_out=qty,
            balance_after=batch.medication.current_stock - qty,
            unit_cost=batch.purchase_price,
            total_value=batch.purchase_price * qty,
            reference_number=_gen_tx_ref(),
            notes=notes or f'Batch {batch.batch_number} disposed.',
            performed_by=request.user,
            transaction_date=timezone.now(),
        )

        batch.quantity_available = 0
        batch.is_active = False
        batch.save()

        label = 'Expired batch' if dispose_type == 'expired_disposal' else 'Damaged stock'
        log_action(
            request.user, AuditLog.Action.DISPOSE, AuditLog.Module.MED_INVENTORY,
            object_type='MedicationBatch', object_id=batch.pk,
            object_repr=f'{batch.medication.name} / {batch.batch_number}',
            description=f'{label} disposed — {qty} units of {batch.medication.name} (batch {batch.batch_number}) removed',
            changes={'quantity': {'old': str(qty), 'new': '0'}},
            extra_data={
                'medication': batch.medication.name,
                'batch_number': batch.batch_number,
                'quantity_disposed': qty,
                'dispose_type': dispose_type,
                'reason': notes,
            },
            request=request,
        )
        messages.success(request, f'{label} ({batch.batch_number}) disposed — {qty} units removed.')
        return redirect('medication_detail', med_id=batch.medication_id)

    return render(request, 'med_inventory/batch_dispose.html', {'batch': batch})


# ── Stock adjustment ─────────────────────────────────────────────────────────

@hms_permission_required('core.manage_medication')
def stock_adjustment(request):
    medications = Medication.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        p      = request.POST
        med_id = p.get('medication')
        adj    = int(p.get('adjustment', 0))
        reason = p.get('reason', '').strip()
        med    = get_object_or_404(Medication, id=med_id, is_active=True)

        if adj == 0:
            messages.error(request, 'Adjustment quantity cannot be zero.')
        else:
            # Find the most recent valid batch and adjust it
            batch = med.batches.filter(is_active=True, quantity_available__gt=0).order_by('expiration_date').first()

            if adj < 0 and abs(adj) > med.current_stock:
                messages.error(request, 'Adjustment would result in negative stock.')
            else:
                tx_type = StockTransaction.TxType.ADJUSTMENT_IN if adj > 0 else StockTransaction.TxType.ADJUSTMENT_OUT

                if batch:
                    batch.quantity_available = max(0, batch.quantity_available + adj)
                    batch.save()
                elif adj > 0:
                    # Create a simple batch entry for adjustments with no batch context
                    from datetime import datetime
                    batch = MedicationBatch.objects.create(
                        medication=med,
                        batch_number=f'ADJ-{med.id}-{date.today().strftime("%Y%m%d")}',
                        expiration_date=date.today().replace(year=date.today().year + 5),
                        quantity_received=adj,
                        quantity_available=adj,
                        purchase_price=med.purchase_price,
                        received_date=date.today(),
                        received_by=request.user,
                        notes=f'Adjustment: {reason}',
                    )

                balance = med.current_stock
                StockTransaction.objects.create(
                    medication=med,
                    batch=batch,
                    transaction_type=tx_type,
                    quantity_in=max(0, adj),
                    quantity_out=max(0, -adj),
                    balance_after=balance,
                    unit_cost=med.purchase_price,
                    total_value=med.purchase_price * abs(adj),
                    reference_number=_gen_tx_ref(),
                    notes=reason,
                    performed_by=request.user,
                    transaction_date=timezone.now(),
                )
                old_stock = balance - adj
                log_action(
                    request.user, AuditLog.Action.ADJUST, AuditLog.Module.MED_INVENTORY,
                    object_type='Medication', object_id=med.pk, object_repr=str(med),
                    description=f'Stock adjusted {adj:+d} units for {med.name}. Reason: {reason}',
                    changes={'quantity': {'old': str(old_stock), 'new': str(balance), 'diff': str(adj)}},
                    extra_data={'medication': med.name, 'adjustment': adj, 'reason': reason,
                                'batch': batch.batch_number if batch else None},
                    request=request,
                )
                messages.success(request, f'Stock adjusted by {adj:+d} units for {med.name}.')
                return redirect('med_inventory_dashboard')

    return render(request, 'med_inventory/stock_adjustment.html', {'medications': medications})


# ── Suppliers ────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def supplier_list(request):
    qs = Supplier.objects.filter(is_active=True)
    q  = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(contact_person__icontains=q))
    paginator = Paginator(qs, 25)
    return render(request, 'med_inventory/supplier_list.html', {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'q': q,
    })


@hms_permission_required('core.manage_medication')
def supplier_create(request):
    if request.method == 'POST':
        p    = request.POST
        name = p.get('name', '').strip()
        code = p.get('code', '').strip()
        errors = []
        if not name: errors.append('Supplier name is required.')
        if not code: errors.append('Supplier code is required.')
        if Supplier.objects.filter(code=code).exists():
            errors.append(f'Code "{code}" already exists.')
        if errors:
            for e in errors: messages.error(request, e)
        else:
            s = Supplier.objects.create(
                name=name, code=code,
                contact_person=p.get('contact_person', '').strip(),
                phone=p.get('phone', '').strip(),
                email=p.get('email', '').strip(),
                address=p.get('address', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.MED_INVENTORY,
                object_type='Supplier', object_id=s.pk, object_repr=s.name,
                description=f'Supplier "{s.name}" ({s.code}) created',
                request=request,
            )
            messages.success(request, f'Supplier "{s.name}" created.')
            return redirect('supplier_list')
    return render(request, 'med_inventory/supplier_form.html', {'action': 'Create', 'post': request.POST})


@hms_permission_required('core.manage_medication')
def supplier_edit(request, supplier_id):
    sup = get_object_or_404(Supplier, id=supplier_id)
    if request.method == 'POST':
        p    = request.POST
        name = p.get('name', '').strip()
        code = p.get('code', '').strip()
        errors = []
        if not name: errors.append('Supplier name is required.')
        if not code: errors.append('Supplier code is required.')
        if Supplier.objects.filter(code=code).exclude(id=supplier_id).exists():
            errors.append(f'Code "{code}" already used.')
        if errors:
            for e in errors: messages.error(request, e)
        else:
            sup.name           = name
            sup.code           = code
            sup.contact_person = p.get('contact_person', '').strip()
            sup.phone          = p.get('phone', '').strip()
            sup.email          = p.get('email', '').strip()
            sup.address        = p.get('address', '').strip()
            sup.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.MED_INVENTORY,
                object_type='Supplier', object_id=sup.pk, object_repr=sup.name,
                description=f'Supplier "{sup.name}" ({sup.code}) updated',
                request=request,
            )
            messages.success(request, f'Supplier "{sup.name}" updated.')
            return redirect('supplier_list')
    return render(request, 'med_inventory/supplier_form.html', {'action': 'Edit', 'sup': sup})


# ── Transaction log ───────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def transaction_list(request):
    qs = StockTransaction.objects.select_related('medication', 'batch', 'performed_by', 'patient')

    q        = request.GET.get('q', '').strip()
    tx_type  = request.GET.get('type', '')
    med_id   = request.GET.get('med', '')
    date_from = request.GET.get('from', '')
    date_to   = request.GET.get('to', '')

    if q:
        qs = qs.filter(
            Q(medication__name__icontains=q)
            | Q(reference_number__icontains=q)
            | Q(batch__batch_number__icontains=q)
        )
    if tx_type:
        qs = qs.filter(transaction_type=tx_type)
    if med_id:
        qs = qs.filter(medication_id=med_id)
    if date_from:
        qs = qs.filter(transaction_date__date__gte=date_from)
    if date_to:
        qs = qs.filter(transaction_date__date__lte=date_to)

    paginator = Paginator(qs, 30)
    return render(request, 'med_inventory/transaction_list.html', {
        'page_obj':    paginator.get_page(request.GET.get('page')),
        'tx_types':    StockTransaction.TxType.choices,
        'medications': Medication.objects.filter(is_active=True).order_by('name'),
        'q':           q,
        'tx_type':     tx_type,
        'med_id':      med_id,
        'date_from':   date_from,
        'date_to':     date_to,
    })


# ── Reports ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def report_stock_on_hand(request):
    today     = date.today()
    d30       = today + timedelta(days=30)
    status_f  = request.GET.get('status', '')
    category  = request.GET.get('category', '')
    q         = request.GET.get('q', '').strip()

    medications = list(
        Medication.objects.filter(is_active=True)
        .select_related('category', 'supplier', 'location')
        .order_by('name')
    )

    if q:
        medications = [m for m in medications if q.lower() in m.name.lower() or q.lower() in m.generic_name.lower()]
    if category:
        medications = [m for m in medications if m.category_id == int(category)]
    if status_f == 'out_of_stock':
        medications = [m for m in medications if m.is_out_of_stock]
    elif status_f == 'low_stock':
        medications = [m for m in medications if m.is_low_stock]
    elif status_f == 'near_expiry':
        near_ids = set(MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__gt=today, expiration_date__lte=d30,
        ).values_list('medication_id', flat=True))
        medications = [m for m in medications if m.id in near_ids]
    elif status_f == 'expired':
        exp_ids = set(MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__lt=today,
        ).values_list('medication_id', flat=True))
        medications = [m for m in medications if m.id in exp_ids]

    total_value = sum(m.inventory_value for m in medications)

    if request.GET.get('export') == 'csv':
        return _export_stock_on_hand_csv(medications, total_value)
    if request.GET.get('export') == 'excel':
        return _export_stock_on_hand_excel(medications, total_value)

    return render(request, 'med_inventory/reports/stock_on_hand.html', {
        'medications': medications,
        'total_value': total_value,
        'categories':  MedicationCategory.objects.order_by('name'),
        'today':       today,
        'status_f':    status_f,
        'category':    category,
        'q':           q,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_expiry(request):
    today  = date.today()
    days   = int(request.GET.get('days', 30))
    limit  = today + timedelta(days=days)

    include_expired = request.GET.get('include_expired', '1') == '1'

    qs = MedicationBatch.objects.filter(
        is_active=True, quantity_available__gt=0,
    ).select_related('medication', 'supplier', 'location').order_by('expiration_date')

    if include_expired:
        qs = qs.filter(expiration_date__lte=limit)
    else:
        qs = qs.filter(expiration_date__gt=today, expiration_date__lte=limit)

    batches = list(qs)

    if request.GET.get('export') == 'csv':
        return _export_expiry_csv(batches, today)
    if request.GET.get('export') == 'excel':
        return _export_expiry_excel(batches, today)

    return render(request, 'med_inventory/reports/expiry.html', {
        'batches':          batches,
        'today':            today,
        'days':             days,
        'include_expired':  include_expired,
        'generated_at':     timezone.now(),
        'generated_by':     request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_stock_movement(request):
    today      = date.today()
    date_from  = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to    = request.GET.get('to', today.isoformat())
    tx_type    = request.GET.get('type', '')
    med_id     = request.GET.get('med', '')

    qs = StockTransaction.objects.select_related(
        'medication', 'batch', 'performed_by', 'patient',
        'source_location', 'dest_location',
    ).order_by('-transaction_date')

    if date_from: qs = qs.filter(transaction_date__date__gte=date_from)
    if date_to:   qs = qs.filter(transaction_date__date__lte=date_to)
    if tx_type:   qs = qs.filter(transaction_type=tx_type)
    if med_id:    qs = qs.filter(medication_id=med_id)

    transactions = list(qs[:500])

    if request.GET.get('export') == 'csv':
        return _export_movement_csv(transactions)
    if request.GET.get('export') == 'excel':
        return _export_movement_excel(transactions)

    return render(request, 'med_inventory/reports/stock_movement.html', {
        'transactions': transactions,
        'tx_types':     StockTransaction.TxType.choices,
        'medications':  Medication.objects.filter(is_active=True).order_by('name'),
        'date_from':    date_from,
        'date_to':      date_to,
        'tx_type':      tx_type,
        'med_id':       med_id,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_low_stock(request):
    medications = [
        m for m in Medication.objects.filter(is_active=True)
        .select_related('category', 'supplier')
        if m.is_low_stock or m.is_out_of_stock
    ]

    if request.GET.get('export') == 'csv':
        return _export_low_stock_csv(medications)
    if request.GET.get('export') == 'excel':
        return _export_low_stock_excel(medications)

    return render(request, 'med_inventory/reports/low_stock.html', {
        'medications':  medications,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_stock_card(request):
    today     = date.today()
    med_id    = request.GET.get('med', '')
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())

    med          = get_object_or_404(Medication, id=med_id) if med_id else None
    medications  = Medication.objects.filter(is_active=True).order_by('name')
    transactions = []

    if med:
        qs = med.transactions.select_related('batch', 'performed_by', 'patient').order_by('transaction_date')
        if date_from: qs = qs.filter(transaction_date__date__gte=date_from)
        if date_to:   qs = qs.filter(transaction_date__date__lte=date_to)
        transactions = list(qs)

        if request.GET.get('export') == 'csv':
            return _export_stock_card_csv(med, transactions)

    return render(request, 'med_inventory/reports/stock_card.html', {
        'med':          med,
        'medications':  medications,
        'transactions': transactions,
        'date_from':    date_from,
        'date_to':      date_to,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_valuation(request):
    medications = list(
        Medication.objects.filter(is_active=True)
        .select_related('category')
        .order_by('name')
    )
    for m in medications:
        m._stock   = m.current_stock
        m._value   = m.inventory_value
        m._expired_qty   = sum(b.quantity_available for b in m.expired_batches)
        m._expired_value = m.purchase_price * m._expired_qty

    total_value   = sum(m._value for m in medications)
    expired_value = sum(m._expired_value for m in medications)

    if request.GET.get('export') == 'csv':
        return _export_valuation_csv(medications, total_value, expired_value)
    if request.GET.get('export') == 'excel':
        return _export_valuation_excel(medications, total_value, expired_value)

    return render(request, 'med_inventory/reports/valuation.html', {
        'medications':   medications,
        'total_value':   total_value,
        'expired_value': expired_value,
        'generated_at':  timezone.now(),
        'generated_by':  request.user,
    })


# ── CSV / Excel export helpers ────────────────────────────────────────────────

def _csv_response(filename):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _excel_response(filename):
    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _export_stock_on_hand_csv(medications, total_value):
    resp = _csv_response(f'stock_on_hand_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow(['Code', 'Medication', 'Generic Name', 'Category', 'Drug Type',
                'Current Stock', 'Unit', 'Status', 'Reorder Level',
                'Purchase Price (ETB)', 'Stock Value (ETB)', 'Location'])
    for m in medications:
        w.writerow([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m.drug_type, m.current_stock, m.unit_of_measure,
            m.stock_status, m.reorder_level,
            m.purchase_price, m.inventory_value,
            str(m.location) if m.location else '',
        ])
    w.writerow([])
    w.writerow(['', '', '', '', '', '', '', '', 'TOTAL VALUE:', '', total_value, ''])
    return resp


def _export_stock_on_hand_excel(medications, total_value):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Stock on Hand'
    headers = ['Code', 'Medication', 'Generic Name', 'Category', 'Drug Type',
               'Stock', 'Unit', 'Status', 'Reorder Lvl', 'Price (ETB)', 'Value (ETB)', 'Location']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor='1E40AF')
        cell.font = Font(bold=True, color='FFFFFF')
    for m in medications:
        ws.append([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m.drug_type, m.current_stock, m.unit_of_measure,
            m.stock_status, m.reorder_level,
            float(m.purchase_price), float(m.inventory_value),
            str(m.location) if m.location else '',
        ])
    ws.append([])
    ws.append(['', '', '', '', '', '', '', '', 'Total Value:', '', float(total_value), ''])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    resp = _excel_response(f'stock_on_hand_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_expiry_csv(batches, today):
    resp = _csv_response(f'expiry_report_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow(['Medication', 'Generic Name', 'Batch Number', 'Lot Number',
                'Qty Available', 'Unit', 'Mfg Date', 'Expiry Date', 'Days Remaining',
                'Status', 'Supplier', 'Location'])
    for b in batches:
        w.writerow([
            b.medication.name, b.medication.generic_name,
            b.batch_number, b.lot_number,
            b.quantity_available, b.medication.unit_of_measure,
            b.manufacturing_date or '', b.expiration_date,
            b.days_to_expiry, b.expiry_status,
            str(b.supplier) if b.supplier else '',
            str(b.location) if b.location else '',
        ])
    return resp


def _export_expiry_excel(batches, today):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Expiry Report'
    headers = ['Medication', 'Generic Name', 'Batch No.', 'Lot No.',
               'Qty', 'Unit', 'Mfg Date', 'Expiry Date', 'Days Left', 'Status', 'Supplier', 'Location']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='DC2626')
    for b in batches:
        ws.append([
            b.medication.name, b.medication.generic_name,
            b.batch_number, b.lot_number,
            b.quantity_available, b.medication.unit_of_measure,
            str(b.manufacturing_date or ''), str(b.expiration_date),
            b.days_to_expiry, b.expiry_status,
            str(b.supplier) if b.supplier else '',
            str(b.location) if b.location else '',
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    resp = _excel_response(f'expiry_report_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_movement_csv(transactions):
    resp = _csv_response(f'stock_movement_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow(['Date', 'Medication', 'Batch', 'Transaction Type',
                'Qty In', 'Qty Out', 'Balance After', 'Unit Cost', 'Total Value',
                'Reference', 'Patient', 'Performed By', 'Notes'])
    for tx in transactions:
        w.writerow([
            tx.transaction_date.strftime('%Y-%m-%d %H:%M'),
            tx.medication.name,
            tx.batch.batch_number if tx.batch else '',
            tx.get_transaction_type_display(),
            tx.quantity_in, tx.quantity_out, tx.balance_after,
            tx.unit_cost, tx.total_value,
            tx.reference_number,
            str(tx.patient) if tx.patient else '',
            tx.performed_by.get_full_name() or tx.performed_by.username,
            tx.notes,
        ])
    return resp


def _export_movement_excel(transactions):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Stock Movement'
    headers = ['Date', 'Medication', 'Batch', 'Type', 'Qty In', 'Qty Out',
               'Balance', 'Unit Cost', 'Total', 'Reference', 'Patient', 'By', 'Notes']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='0F766E')
    for tx in transactions:
        ws.append([
            tx.transaction_date.strftime('%Y-%m-%d %H:%M'),
            tx.medication.name,
            tx.batch.batch_number if tx.batch else '',
            tx.get_transaction_type_display(),
            tx.quantity_in, tx.quantity_out, tx.balance_after,
            float(tx.unit_cost), float(tx.total_value),
            tx.reference_number,
            str(tx.patient) if tx.patient else '',
            tx.performed_by.get_full_name() or tx.performed_by.username,
            tx.notes,
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    resp = _excel_response(f'stock_movement_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_low_stock_csv(medications):
    resp = _csv_response(f'low_stock_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow(['Code', 'Medication', 'Generic Name', 'Category',
                'Current Stock', 'Reorder Level', 'Reorder Qty', 'Status', 'Supplier'])
    for m in medications:
        w.writerow([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m.current_stock, m.reorder_level, m.reorder_quantity,
            m.stock_status,
            m.supplier.name if m.supplier else '',
        ])
    return resp


def _export_low_stock_excel(medications):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Low Stock'
    headers = ['Code', 'Medication', 'Generic Name', 'Category',
               'Current Stock', 'Reorder Level', 'Reorder Qty', 'Status', 'Supplier']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='D97706')
    for m in medications:
        ws.append([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m.current_stock, m.reorder_level, m.reorder_quantity,
            m.stock_status,
            m.supplier.name if m.supplier else '',
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    resp = _excel_response(f'low_stock_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_valuation_csv(medications, total_value, expired_value):
    resp = _csv_response(f'inventory_valuation_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow(['Code', 'Medication', 'Generic Name', 'Category',
                'Stock Qty', 'Unit', 'Unit Cost (ETB)', 'Total Value (ETB)',
                'Expired Qty', 'Expired Value (ETB)'])
    for m in medications:
        w.writerow([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m._stock, m.unit_of_measure,
            m.purchase_price, m._value,
            m._expired_qty, m._expired_value,
        ])
    w.writerow([])
    w.writerow(['', '', '', '', 'TOTAL:', '', '', total_value, '', expired_value])
    return resp


def _export_valuation_excel(medications, total_value, expired_value):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Inventory Valuation'
    headers = ['Code', 'Medication', 'Generic Name', 'Category',
               'Stock Qty', 'Unit', 'Unit Cost (ETB)', 'Total Value (ETB)',
               'Expired Qty', 'Expired Value (ETB)']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='7C3AED')
    for m in medications:
        ws.append([
            m.code, m.name, m.generic_name,
            m.category.name if m.category else '',
            m._stock, m.unit_of_measure,
            float(m.purchase_price), float(m._value),
            m._expired_qty, float(m._expired_value),
        ])
    ws.append([])
    ws.append(['', '', '', '', 'TOTAL:', '', '', float(total_value), '', float(expired_value)])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 20
    resp = _excel_response(f'inventory_valuation_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_stock_card_csv(med, transactions):
    resp = _csv_response(f'stock_card_{med.code}_{date.today()}.csv')
    w = csv.writer(resp)
    w.writerow([f'Stock Card — {med.name} ({med.generic_name}) — {med.code}'])
    w.writerow([])
    w.writerow(['Date', 'Transaction Type', 'Batch', 'Qty In', 'Qty Out',
                'Balance', 'Unit Cost', 'Total Value', 'Reference', 'Performed By', 'Notes'])
    for tx in transactions:
        w.writerow([
            tx.transaction_date.strftime('%Y-%m-%d %H:%M'),
            tx.get_transaction_type_display(),
            tx.batch.batch_number if tx.batch else '',
            tx.quantity_in, tx.quantity_out, tx.balance_after,
            tx.unit_cost, tx.total_value, tx.reference_number,
            tx.performed_by.get_full_name() or tx.performed_by.username,
            tx.notes,
        ])
    return resp
