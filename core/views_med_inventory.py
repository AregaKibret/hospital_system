"""Medication Inventory Management — views."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_action, build_changes
from .decorators import hms_permission_required
from .models import (
    AuditLog, GoodsReceipt, InventoryCategory, InventoryItem, MedicationBatch, Medication,
    Patient, PharmacyCreditSettings, PricingSettings, StockTransaction, StorageLocation, Supplier,
    SupplierPayable, SupplierPayment,
)
from .notifications import notify_role

# ── helpers ──────────────────────────────────────────────────────────────────

def _alert_counts():
    today = date.today()
    d30   = today + timedelta(days=30)
    d90   = today + timedelta(days=90)

    low_stock = sum(
        1 for m in Medication.objects.filter(inventory_item__is_active=True)
        if m.is_low_stock
    )
    out_of_stock = sum(
        1 for m in Medication.objects.filter(inventory_item__is_active=True)
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


def _payable_alerts():
    """Due-soon / overdue supplier payable counts, computed live on every call
    (dashboard-alerts-only — no scheduler exists in this codebase)."""
    today = date.today()
    reminder_days = PharmacyCreditSettings.get_solo().reminder_days_before_due
    due_soon_limit = today + timedelta(days=reminder_days)

    open_payables = SupplierPayable.objects.select_related('supplier', 'goods_receipt').filter(
        amount_paid__lt=F('total_amount'),
    )
    overdue = [p for p in open_payables if p.is_overdue]
    due_soon = [
        p for p in open_payables
        if not p.is_overdue and p.due_date and today <= p.due_date <= due_soon_limit
    ]
    return {'overdue': overdue, 'due_soon': due_soon}


# ── AJAX search API ────────────────────────────────────────────────────────────

@hms_permission_required('core.receive_stock')
def med_inventory_search_api(request):
    """Drug search for the Goods Receipt cart / quick-receive screens.
    Mirrors views_prescription.medication_search_api's filter logic, but
    returns the auto-populate fields a receiving clerk needs instead of the
    dispensing-focused fields that view returns."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    meds = (
        Medication.objects
        .filter(inventory_item__is_active=True)
        .filter(
            Q(brand_name__icontains=q)
            | Q(generic_name__icontains=q)
            | Q(scientific_name__icontains=q)
            | Q(inventory_item__item_code__icontains=q)
            | Q(inventory_item__barcode__icontains=q)
        )
        .select_related('inventory_item', 'inventory_item__category')
        .order_by('brand_name')[:20]
    )

    results = []
    for m in meds:
        results.append({
            'id': m.pk,
            'code': m.code,
            'generic_name': m.generic_name,
            'name': m.name,
            'strength': m.strength,
            'unit_of_measure': m.unit_of_measure,
            'purchase_unit': m.purchase_unit,
            'dispensing_unit': m.dispensing_unit,
            'selling_price': str(m.selling_price),
            'purchase_price': str(m.purchase_price),
            'category': m.category.name if m.category else '',
            'manufacturer': m.manufacturer,
            'current_stock': m.current_stock,
        })
    return JsonResponse({'results': results})


# ── Pricing Settings (Administrator) ──────────────────────────────────────────

def _resolve_selling_price(purchase_price, submitted_selling_price, is_overridden):
    """Applies the global default markup unless the user explicitly overrode
    the Selling Price field. Returns (final_price, is_manual, computed_price)."""
    computed = PricingSettings.get_solo().compute_selling_price(purchase_price)
    if is_overridden:
        try:
            final = Decimal(submitted_selling_price)
        except Exception:
            final = computed
        return final, True, computed
    return computed, False, computed


@hms_permission_required('core.system_configuration')
def pricing_settings_edit(request):
    pricing = PricingSettings.get_solo()

    if request.method == 'POST':
        form_action = request.POST.get('form_action', 'save_markup')

        if form_action == 'save_markup':
            old_markup = pricing.default_markup_percent
            try:
                new_markup = Decimal(request.POST.get('default_markup_percent', '25'))
                if new_markup < 0 or new_markup > 1000:
                    raise ValueError('Markup must be between 0 and 1000%.')
            except Exception:
                messages.error(request, 'Enter a valid markup percentage (0–1000).')
            else:
                pricing.default_markup_percent = new_markup
                pricing.updated_by = request.user
                pricing.save()
                log_action(
                    request.user, AuditLog.Action.UPDATE, AuditLog.Module.ADMIN,
                    object_type='PricingSettings', object_id=pricing.pk,
                    object_repr='Pricing Settings',
                    description=f'Default markup changed from {old_markup}% to {new_markup}%',
                    changes={'default_markup_percent': {'old': str(old_markup), 'new': str(new_markup)}},
                    request=request,
                )
                messages.success(
                    request,
                    f'Default markup updated to {new_markup}%. This applies to newly '
                    f'created/received stock only — existing prices are unchanged unless '
                    f'you recalculate them below.',
                )
            return redirect('pricing_settings_edit')

        elif form_action == 'recalculate':
            scope = request.POST.getlist('scope')
            med_updated = med_skipped = batch_updated = batch_skipped = item_updated = item_skipped = 0

            with transaction.atomic():
                if 'medication' in scope:
                    # Medications are InventoryItems (item_type=Medication) —
                    # their selling_price/selling_price_is_manual now live
                    # only on InventoryItem, so this recalculates there.
                    for item in InventoryItem.objects.filter(item_type=InventoryItem.ItemType.MEDICATION, is_active=True):
                        if item.selling_price_is_manual:
                            med_skipped += 1
                            continue
                        new_price = pricing.compute_selling_price(item.unit_cost)
                        if new_price != item.selling_price:
                            item.selling_price = new_price
                            item.save(update_fields=['selling_price'])
                            med_updated += 1

                if 'batch' in scope:
                    for batch in MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0):
                        if batch.selling_price_is_manual:
                            batch_skipped += 1
                            continue
                        new_price = pricing.compute_selling_price(batch.purchase_price)
                        if new_price != batch.selling_price:
                            batch.selling_price = new_price
                            batch.save(update_fields=['selling_price'])
                            batch_updated += 1

                if 'inventory' in scope:
                    # Excludes Medication-typed items — those are covered by
                    # the 'medication' scope above to avoid double-processing.
                    for item in InventoryItem.objects.filter(is_active=True).exclude(item_type=InventoryItem.ItemType.MEDICATION):
                        if item.selling_price_is_manual:
                            item_skipped += 1
                            continue
                        new_price = pricing.compute_selling_price(item.unit_cost)
                        if new_price != item.selling_price:
                            item.selling_price = new_price
                            item.save(update_fields=['selling_price'])
                            item_updated += 1

            total_updated = med_updated + batch_updated + item_updated
            total_skipped = med_skipped + batch_skipped + item_skipped
            log_action(
                request.user, AuditLog.Action.ADJUST, AuditLog.Module.ADMIN,
                object_type='PricingSettings', object_id=pricing.pk,
                object_repr='Pricing Settings',
                description=(
                    f'Bulk recalculation at {pricing.default_markup_percent}% markup: '
                    f'{total_updated} price(s) updated, {total_skipped} manually-priced '
                    f'record(s) skipped'
                ),
                extra_data={
                    'scope': scope, 'medications_updated': med_updated, 'medications_skipped': med_skipped,
                    'batches_updated': batch_updated, 'batches_skipped': batch_skipped,
                    'inventory_items_updated': item_updated, 'inventory_items_skipped': item_skipped,
                },
                request=request,
            )
            messages.success(
                request,
                f'Recalculated {total_updated} selling price(s) at {pricing.default_markup_percent}% markup. '
                f'{total_skipped} record(s) with a manually-set price were left untouched.',
            )
            return redirect('pricing_settings_edit')

    med_items = InventoryItem.objects.filter(item_type=InventoryItem.ItemType.MEDICATION, is_active=True)
    non_med_items = InventoryItem.objects.filter(is_active=True).exclude(item_type=InventoryItem.ItemType.MEDICATION)
    return render(request, 'med_inventory/pricing_settings.html', {
        'pricing': pricing,
        'medication_count': med_items.filter(selling_price_is_manual=False).count(),
        'medication_manual_count': med_items.filter(selling_price_is_manual=True).count(),
        'batch_count': MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0, selling_price_is_manual=False).count(),
        'batch_manual_count': MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0, selling_price_is_manual=True).count(),
        'item_count': non_med_items.filter(selling_price_is_manual=False).count(),
        'item_manual_count': non_med_items.filter(selling_price_is_manual=True).count(),
    })


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def med_inventory_dashboard(request):
    today = date.today()
    d30   = today + timedelta(days=30)
    d90   = today + timedelta(days=90)

    total_meds      = Medication.objects.filter(inventory_item__is_active=True).count()
    total_suppliers = Supplier.objects.filter(is_active=True).count()
    total_batches   = MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0).count()

    medications     = list(Medication.objects.filter(inventory_item__is_active=True).select_related('inventory_item__category', 'inventory_item__supplier'))
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

    # Dashboard-alerts-only: computed live on every page load (no scheduler
    # exists in this codebase). Deduped against unread notifications for the
    # same payable so re-visiting the dashboard doesn't spam the bell inbox.
    payable_alerts = _payable_alerts()
    from .models import Notification
    notified_ids = set(
        Notification.objects.filter(
            object_type='SupplierPayable', is_read=False,
        ).values_list('object_id', flat=True)
    )
    for p in payable_alerts['overdue']:
        if p.pk not in notified_ids:
            notify_role(
                'Pharmacy Admin',
                f'Overdue Supplier Payment — {p.supplier.name}',
                f'{p.goods_receipt.goods_receipt_number}: ETB {p.outstanding_balance:,.2f} overdue since {p.due_date}',
                notif_type='inventory',
                url=f"/med-inventory/payables/{p.pk}/",
                object_type='SupplierPayable', object_id=p.pk,
            )
    for p in payable_alerts['due_soon']:
        if p.pk not in notified_ids:
            notify_role(
                'Pharmacy Admin',
                f'Supplier Payment Due Soon — {p.supplier.name}',
                f'{p.goods_receipt.goods_receipt_number}: ETB {p.outstanding_balance:,.2f} due {p.due_date}',
                notif_type='inventory',
                url=f"/med-inventory/payables/{p.pk}/",
                object_type='SupplierPayable', object_id=p.pk,
            )

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
        'overdue_payables':  payable_alerts['overdue'],
        'due_soon_payables': payable_alerts['due_soon'],
        'payables_alert_count': len(payable_alerts['overdue']) + len(payable_alerts['due_soon']),
    })


# ── Medication CRUD ──────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def medication_list(request):
    qs = Medication.objects.filter(inventory_item__is_active=True).select_related(
        'inventory_item', 'inventory_item__category', 'inventory_item__supplier',
    )

    q        = request.GET.get('q', '').strip()
    category = request.GET.get('category', '')
    status   = request.GET.get('status', '')
    drug_type = request.GET.get('drug_type', '')

    if q:
        qs = qs.filter(
            Q(brand_name__icontains=q) | Q(generic_name__icontains=q)
            | Q(inventory_item__item_code__icontains=q) | Q(inventory_item__barcode__icontains=q)
        )
    if category:
        qs = qs.filter(inventory_item__category_id=category)
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
        'categories': InventoryCategory.objects.order_by('name'),
        'drug_types': Medication.DrugType.choices,
        'q':          q,
        'category':   category,
        'status':     status,
        'drug_type':  drug_type,
        'alerts':     _alert_counts(),
    })


@hms_permission_required('core.manage_medication')
def medication_create(request):
    categories = InventoryCategory.objects.order_by('name')
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
        if InventoryItem.objects.filter(item_code=code).exists():
            errors.append(f'Code "{code}" already exists.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            purchase_price = Decimal(p.get('purchase_price') or '0')
            is_overridden = bool(p.get('selling_price_overridden'))
            selling_price, is_manual, computed_price = _resolve_selling_price(
                purchase_price, p.get('selling_price'), is_overridden,
            )

            # Single combined submit creates the Item Master row and its
            # linked Medication Details row together — no duplicate data
            # entry, and the Medication Catalog is, by construction, every
            # InventoryItem with item_type=Medication.
            with transaction.atomic():
                item = InventoryItem.objects.create(
                    name=name,
                    item_code=code,
                    generic_name=generic_name,
                    item_type=InventoryItem.ItemType.MEDICATION,
                    category_id=p.get('category') or None,
                    dosage_form=p.get('dosage_form', '').strip(),
                    strength=strength,
                    barcode=p.get('barcode', '').strip(),
                    manufacturer=p.get('manufacturer', '').strip(),
                    unit=p.get('unit_of_measure', 'Tablet').strip(),
                    unit_purchase=p.get('purchase_unit', 'Box').strip(),
                    dispensing_unit=p.get('dispensing_unit', 'Tablet').strip(),
                    consumption_factor=int(p.get('conversion_factor') or 1),
                    unit_cost=purchase_price,
                    selling_price=selling_price,
                    selling_price_is_manual=is_manual,
                    reorder_level=int(p.get('reorder_level') or 0),
                    reorder_quantity=int(p.get('reorder_quantity') or 0),
                    min_stock=int(p.get('minimum_stock') or 0),
                    max_stock=int(p.get('maximum_stock') or 0) or None,
                    safety_stock=int(p.get('safety_stock') or 0),
                    supplier_id=p.get('supplier') or None,
                    storage_location_id=p.get('location') or None,
                    is_active=True,
                )
                med = Medication.objects.create(
                    inventory_item=item,
                    brand_name=name,
                    generic_name=generic_name,
                    scientific_name=p.get('scientific_name', '').strip(),
                    therapeutic_class=p.get('therapeutic_class', '').strip(),
                    drug_type=p.get('drug_type', Medication.DrugType.TABLET),
                    strength=strength,
                    dosage_form=p.get('dosage_form', '').strip(),
                    route=p.get('route', Medication.Route.ORAL),
                    country_of_origin=p.get('country_of_origin', '').strip(),
                    pack_description=p.get('pack_description', '').strip(),
                    units_per_pack=int(p.get('units_per_pack') or 1),
                    wholesale_price=Decimal(p.get('wholesale_price')) if p.get('wholesale_price') else None,
                    insurance_price=Decimal(p.get('insurance_price')) if p.get('insurance_price') else None,
                    registration_number=p.get('registration_number', '').strip(),
                    regulatory_approval=p.get('regulatory_approval', '').strip(),
                    controlled_substance=bool(p.get('controlled_substance')),
                    prescription_required=bool(p.get('prescription_required', True)),
                    storage_condition=p.get('storage_condition', Medication.StorageCondition.ROOM_TEMP),
                    notes=p.get('notes', '').strip(),
                    atc_code=p.get('atc_code', '').strip(),
                )
                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.MED_INVENTORY,
                    object_type='Medication', object_id=med.pk, object_repr=str(med),
                    description=f'Medication "{med.name}" ({med.code}) created',
                    extra_data={'code': med.code, 'strength': med.strength,
                                'selling_price': str(med.selling_price)},
                    request=request,
                )
                if is_manual:
                    log_action(
                        request.user, AuditLog.Action.ADJUST, AuditLog.Module.MED_INVENTORY,
                        object_type='Medication', object_id=med.pk, object_repr=str(med),
                        description=(
                            f'Selling price manually overridden for {med.name}: '
                            f'ETB {computed_price} (auto) → ETB {selling_price} (manual)'
                        ),
                        changes={'selling_price': {'old': str(computed_price), 'new': str(selling_price)}},
                        extra_data={'auto_calculated': str(computed_price), 'manual_override': str(selling_price)},
                        request=request,
                    )
            messages.success(request, f'Medication "{med.name}" created successfully.')
            return redirect('medication_detail', med_id=med.id)

    empty_post = {
        'name': '', 'generic_name': '', 'scientific_name': '', 'code': '', 'barcode': '',
        'category': '', 'therapeutic_class': '', 'drug_type': '', 'strength': '',
        'dosage_form': '', 'manufacturer': '', 'country_of_origin': '',
    }
    return render(request, 'med_inventory/medication_form.html', {
        'action':     'Create',
        'categories': categories,
        'suppliers':  suppliers,
        'locations':  locations,
        'drug_types': Medication.DrugType.choices,
        'routes':     Medication.Route.choices,
        'conditions': Medication.StorageCondition.choices,
        'post':       {**empty_post, **request.POST.dict()},
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
    })


@hms_permission_required('core.manage_medication')
def medication_edit(request, med_id):
    med        = get_object_or_404(Medication.objects.select_related('inventory_item'), id=med_id)
    item       = med.inventory_item
    categories = InventoryCategory.objects.order_by('name')
    suppliers  = Supplier.objects.filter(is_active=True).order_by('name')
    locations  = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []
        name = p.get('name', '').strip()
        code = p.get('code', '').strip()

        if not name: errors.append('Medication name is required.')
        if not code: errors.append('Medication code is required.')
        if InventoryItem.objects.filter(item_code=code).exclude(id=item.id).exists():
            errors.append(f'Code "{code}" is already used by another medication.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # Capture old values for audit trail before mutating
            _track_fields = ['brand_name', 'generic_name', 'strength']
            old_med = Medication.objects.get(pk=med_id)
            old_selling_price = item.selling_price

            purchase_price = Decimal(p.get('purchase_price') or '0')
            is_overridden = bool(p.get('selling_price_overridden'))
            selling_price, is_manual, computed_price = _resolve_selling_price(
                purchase_price, p.get('selling_price'), is_overridden,
            )

            with transaction.atomic():
                item.name               = name
                item.item_code          = code
                item.generic_name       = p.get('generic_name', '').strip()
                item.category_id        = p.get('category') or None
                item.dosage_form        = p.get('dosage_form', '').strip()
                item.strength           = p.get('strength', '').strip()
                item.barcode            = p.get('barcode', '').strip()
                item.manufacturer       = p.get('manufacturer', '').strip()
                item.unit               = p.get('unit_of_measure', 'Tablet').strip()
                item.unit_purchase      = p.get('purchase_unit', 'Box').strip()
                item.dispensing_unit    = p.get('dispensing_unit', 'Tablet').strip()
                item.consumption_factor = int(p.get('conversion_factor') or 1)
                item.unit_cost          = purchase_price
                item.selling_price      = selling_price
                item.selling_price_is_manual = is_manual
                item.reorder_level      = int(p.get('reorder_level') or 0)
                item.reorder_quantity   = int(p.get('reorder_quantity') or 0)
                item.min_stock          = int(p.get('minimum_stock') or 0)
                item.max_stock          = int(p.get('maximum_stock') or 0) or None
                item.safety_stock       = int(p.get('safety_stock') or 0)
                item.supplier_id        = p.get('supplier') or None
                item.storage_location_id = p.get('location') or None
                item.save()

                med.brand_name          = name
                med.generic_name        = p.get('generic_name', '').strip()
                med.scientific_name     = p.get('scientific_name', '').strip()
                med.therapeutic_class   = p.get('therapeutic_class', '').strip()
                med.drug_type           = p.get('drug_type', med.drug_type)
                med.strength            = p.get('strength', '').strip()
                med.dosage_form         = p.get('dosage_form', '').strip()
                med.route               = p.get('route', med.route)
                med.country_of_origin   = p.get('country_of_origin', '').strip()
                med.pack_description    = p.get('pack_description', '').strip()
                med.units_per_pack      = int(p.get('units_per_pack') or 1)
                med.wholesale_price     = Decimal(p.get('wholesale_price')) if p.get('wholesale_price') else None
                med.insurance_price     = Decimal(p.get('insurance_price')) if p.get('insurance_price') else None
                med.registration_number = p.get('registration_number', '').strip()
                med.regulatory_approval = p.get('regulatory_approval', '').strip()
                med.controlled_substance = bool(p.get('controlled_substance'))
                med.prescription_required = bool(p.get('prescription_required', True))
                med.storage_condition   = p.get('storage_condition', med.storage_condition)
                med.notes               = p.get('notes', '').strip()
                med.atc_code            = p.get('atc_code', '').strip()
                med.save()

                changes = build_changes(old_med, med, _track_fields)
                log_action(
                    request.user, AuditLog.Action.UPDATE, AuditLog.Module.MED_INVENTORY,
                    object_type='Medication', object_id=med.pk, object_repr=str(med),
                    description=f'Medication "{med.name}" ({med.code}) updated',
                    changes=changes,
                    request=request,
                )
                if is_manual and str(selling_price) != str(old_selling_price):
                    log_action(
                        request.user, AuditLog.Action.ADJUST, AuditLog.Module.MED_INVENTORY,
                        object_type='Medication', object_id=med.pk, object_repr=str(med),
                        description=(
                            f'Selling price manually overridden for {med.name}: '
                            f'ETB {old_selling_price} → ETB {selling_price} (auto would be ETB {computed_price})'
                        ),
                        changes={'selling_price': {'old': str(old_selling_price), 'new': str(selling_price)}},
                        extra_data={'auto_calculated': str(computed_price), 'manual_override': str(selling_price)},
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
        'post': {
            'name': '', 'generic_name': '', 'scientific_name': '', 'code': '', 'barcode': '',
            'category': '', 'therapeutic_class': '', 'drug_type': '', 'strength': '',
            'dosage_form': '', 'manufacturer': '', 'country_of_origin': '',
            **request.POST.dict(),
        },
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
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
        med = get_object_or_404(Medication.objects.select_related('inventory_item'), id=med_id)
        med.inventory_item.is_active = False
        med.inventory_item.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.DEACTIVATE, AuditLog.Module.MED_INVENTORY,
            object_type='Medication', object_id=med.pk, object_repr=str(med),
            description=f'Medication "{med.name}" ({med.code}) deactivated',
            request=request,
        )
        messages.success(request, f'Medication "{med.name}" deactivated.')
    return redirect('medication_list')


@hms_permission_required('core.manage_medication')
def medication_details_complete(request, item_id):
    """Cross-module consistency guard: reached when an InventoryItem is set
    to item_type=Medication from the general Inventory module (rather than
    Pharmacy's combined "Add Medication" form) and has no linked Medication
    Details yet — without this, the item would silently be unavailable for
    prescribing/dispensing (no strength/route/prescription-required data)."""
    item = get_object_or_404(InventoryItem, pk=item_id, item_type=InventoryItem.ItemType.MEDICATION)
    existing = Medication.objects.filter(inventory_item=item).first()
    if existing:
        return redirect('medication_detail', med_id=existing.id)

    if request.method == 'POST':
        p = request.POST
        errors = []
        generic_name = p.get('generic_name', '').strip()
        strength = p.get('strength', '').strip()
        if not generic_name: errors.append('Generic name is required.')
        if not strength:     errors.append('Strength is required.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            med = Medication.objects.create(
                inventory_item=item,
                brand_name=item.name,
                generic_name=generic_name,
                scientific_name=p.get('scientific_name', '').strip(),
                therapeutic_class=p.get('therapeutic_class', '').strip(),
                drug_type=p.get('drug_type', Medication.DrugType.TABLET),
                strength=strength,
                dosage_form=p.get('dosage_form', '').strip() or item.dosage_form,
                route=p.get('route', Medication.Route.ORAL),
                country_of_origin=p.get('country_of_origin', '').strip(),
                prescription_required=bool(p.get('prescription_required', True)),
                controlled_substance=bool(p.get('controlled_substance')),
                storage_condition=p.get('storage_condition', Medication.StorageCondition.ROOM_TEMP),
                notes=p.get('notes', '').strip(),
                atc_code=p.get('atc_code', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.MED_INVENTORY,
                object_type='Medication', object_id=med.pk, object_repr=str(med),
                description=f'Medication Details completed for Item Master entry "{item.name}" ({item.item_code})',
                request=request,
            )
            messages.success(request, f'Medication Details saved for "{item.name}". It is now available in the Medication Catalog.')
            return redirect('medication_detail', med_id=med.id)

    return render(request, 'med_inventory/medication_details_complete.html', {
        'item':       item,
        'drug_types': Medication.DrugType.choices,
        'routes':     Medication.Route.choices,
        'conditions': Medication.StorageCondition.choices,
    })


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
            | Q(medication__brand_name__icontains=q)
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


@hms_permission_required('core.receive_stock')
def batch_receive_select(request):
    """Landing page to pick which medication to receive stock for (the
    single-drug quick-receive fast path). For a multi-drug supplier invoice,
    use the Goods Receipt cart screen instead."""
    medications = Medication.objects.filter(inventory_item__is_active=True).select_related(
        'inventory_item', 'inventory_item__category',
    ).order_by('brand_name')
    q = request.GET.get('q', '').strip()
    if q:
        medications = medications.filter(
            Q(brand_name__icontains=q) | Q(generic_name__icontains=q)
            | Q(inventory_item__item_code__icontains=q) | Q(inventory_item__barcode__icontains=q)
        )
    return render(request, 'med_inventory/batch_receive_select.html', {'medications': medications, 'q': q})


@hms_permission_required('core.receive_stock')
def batch_receive(request, med_id):
    """Single-drug quick-receive fast path. Internally creates the same
    GoodsReceipt (one line item) + SupplierPayable (if Credit/Consignment)
    records as the multi-drug cart, so both paths are indistinguishable in
    reporting afterward."""
    med       = get_object_or_404(Medication, id=med_id, inventory_item__is_active=True)
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    locations = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []
        batch_number   = p.get('batch_number', '').strip()
        expiry_str     = p.get('expiration_date', '').strip()
        qty_str        = p.get('quantity', '').strip()
        supplier_id    = p.get('supplier') or None
        invoice_number = p.get('invoice_number', '').strip()
        payment_method = p.get('payment_method', GoodsReceipt.PaymentMethod.CASH)

        if not batch_number:   errors.append('Batch number is required.')
        if not expiry_str:     errors.append('Expiration date is required.')
        if not qty_str:        errors.append('Quantity is required.')
        if not supplier_id:    errors.append('Supplier is required.')
        if not invoice_number: errors.append('Supplier invoice number is required.')

        if not errors:
            from datetime import datetime
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            if expiry_date <= date.today():
                errors.append('Expiration date must be in the future.')
            if MedicationBatch.objects.filter(medication=med, batch_number=batch_number).exists():
                errors.append(f'Batch number "{batch_number}" already exists for this medication.')
            if GoodsReceipt.objects.filter(supplier_id=supplier_id, supplier_invoice_number=invoice_number).exists():
                errors.append(f'Invoice number "{invoice_number}" has already been recorded for this supplier.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            qty           = int(qty_str)
            unit_cost     = Decimal(p.get('purchase_price') or str(med.purchase_price))
            is_overridden = bool(p.get('selling_price_overridden'))
            selling_price, is_manual, computed_price = _resolve_selling_price(
                unit_cost, p.get('selling_price'), is_overridden,
            )
            status        = p.get('status', MedicationBatch.BatchStatus.ACTIVE)
            rec_date      = p.get('received_date') or date.today().isoformat()
            from datetime import datetime
            rec_date  = datetime.strptime(rec_date, '%Y-%m-%d').date()
            total_cost = unit_cost * qty

            with transaction.atomic():
                receipt = GoodsReceipt.objects.create(
                    supplier_id=supplier_id,
                    supplier_invoice_number=invoice_number,
                    supplier_invoice_date=p.get('supplier_invoice_date') or None,
                    purchase_order_ref=p.get('po_ref', '').strip(),
                    payment_method=payment_method,
                    received_by=request.user,
                    notes=p.get('notes', '').strip(),
                )

                if payment_method == GoodsReceipt.PaymentMethod.CASH:
                    receipt.cash_payment_date = rec_date
                    receipt.cash_amount_paid = total_cost
                    receipt.cash_cashier = request.user
                    receipt.cash_payment_reference = p.get('cash_payment_reference', '').strip()
                    receipt.save()
                    ownership = MedicationBatch.Ownership.PURCHASED
                else:
                    due_date = p.get('due_date') or None
                    credit_days = int(p.get('credit_period_days')) if p.get('credit_period_days') else None
                    if not due_date and credit_days:
                        due_date = rec_date + timedelta(days=credit_days)
                    SupplierPayable.objects.create(
                        goods_receipt=receipt,
                        supplier_id=supplier_id,
                        total_amount=total_cost,
                        due_date=due_date,
                        credit_period_days=credit_days,
                        consignment_agreement_number=p.get('consignment_agreement_number', '').strip(),
                    )
                    ownership = (
                        MedicationBatch.Ownership.CONSIGNMENT
                        if payment_method == GoodsReceipt.PaymentMethod.CONSIGNMENT
                        else MedicationBatch.Ownership.PURCHASED
                    )

                batch = MedicationBatch.objects.create(
                    medication=med,
                    batch_number=batch_number,
                    lot_number=p.get('lot_number', '').strip(),
                    manufacturing_date=p.get('manufacturing_date') or None,
                    expiration_date=expiry_date,
                    quantity_received=qty,
                    quantity_available=qty,
                    purchase_price=unit_cost,
                    selling_price=selling_price,
                    selling_price_is_manual=is_manual,
                    supplier_id=supplier_id,
                    location_id=p.get('location') or None,
                    received_date=rec_date,
                    received_by=request.user,
                    purchase_order_ref=p.get('po_ref', '').strip(),
                    invoice_number=invoice_number,
                    goods_receipt=receipt,
                    ownership=ownership,
                    status=status,
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
                    total_value=total_cost,
                    reference_number=_gen_tx_ref(),
                    notes=p.get('notes', '').strip(),
                    performed_by=request.user,
                    transaction_date=timezone.now(),
                )

                log_action(
                    request.user, AuditLog.Action.RECEIVE, AuditLog.Module.MED_INVENTORY,
                    object_type='MedicationBatch', object_id=batch.pk,
                    object_repr=f'{med.name} / {batch_number}',
                    description=f'Received {qty} units of {med.name} — batch {batch_number}, expires {expiry_date} ({receipt.get_payment_method_display()}, {receipt.goods_receipt_number})',
                    changes={'quantity': {'old': '0', 'new': str(qty)}},
                    extra_data={
                        'medication': med.name,
                        'batch_number': batch_number,
                        'quantity': qty,
                        'unit_cost': str(unit_cost),
                        'expiration_date': str(expiry_date),
                        'supplier_id': str(supplier_id or ''),
                        'goods_receipt': receipt.goods_receipt_number,
                        'payment_method': payment_method,
                    },
                    request=request,
                )
                if is_manual:
                    log_action(
                        request.user, AuditLog.Action.ADJUST, AuditLog.Module.MED_INVENTORY,
                        object_type='MedicationBatch', object_id=batch.pk,
                        object_repr=f'{med.name} / {batch_number}',
                        description=(
                            f'Selling price manually overridden for batch {batch_number} ({med.name}): '
                            f'ETB {computed_price} (auto) → ETB {selling_price} (manual)'
                        ),
                        changes={'selling_price': {'old': str(computed_price), 'new': str(selling_price)}},
                        extra_data={'auto_calculated': str(computed_price), 'manual_override': str(selling_price)},
                        request=request,
                    )
            messages.success(request, f'{qty} units of batch {batch_number} received for {med.name} ({receipt.goods_receipt_number}).')
            return redirect('medication_detail', med_id=med.id)

    return render(request, 'med_inventory/batch_form.html', {
        'med':       med,
        'suppliers': suppliers,
        'locations': locations,
        'today':     date.today().isoformat(),
        'payment_methods': GoodsReceipt.PaymentMethod.choices,
        'batch_statuses':  MedicationBatch.BatchStatus.choices,
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
    })


@hms_permission_required('core.receive_stock')
def goods_receipt_create(request):
    """Multi-drug Goods Receipt cart — one supplier invoice with several
    line items entered and submitted once. Each cart line becomes its own
    MedicationBatch + StockTransaction; the whole receipt becomes a single
    GoodsReceipt (+ SupplierPayable if Credit/Consignment)."""
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    locations = StorageLocation.objects.order_by('name')

    if request.method == 'POST':
        p = request.POST
        errors = []

        supplier_id     = p.get('supplier') or None
        invoice_number  = p.get('supplier_invoice_number', '').strip()
        invoice_date    = p.get('supplier_invoice_date') or None
        po_ref          = p.get('purchase_order_ref', '').strip()
        delivery_note   = p.get('delivery_note_number', '').strip()
        payment_method  = p.get('payment_method', GoodsReceipt.PaymentMethod.CASH)
        notes           = p.get('notes', '').strip()

        if not supplier_id:    errors.append('Supplier is required.')
        if not invoice_number: errors.append('Supplier invoice number is required.')
        elif GoodsReceipt.objects.filter(supplier_id=supplier_id, supplier_invoice_number=invoice_number).exists():
            errors.append(f'Invoice number "{invoice_number}" has already been recorded for this supplier.')

        # Collect cart lines: med_{idx} / batch_number_{idx} / expiry_{idx} /
        # qty_{idx} / cost_{idx} / selling_price_{idx} / location_{idx}
        lines = []
        idx = 0
        while True:
            med_id = p.get(f'med_{idx}')
            if med_id is None:
                break
            batch_number = p.get(f'batch_number_{idx}', '').strip()
            qty          = p.get(f'qty_{idx}', '').strip()
            expiry       = p.get(f'expiry_{idx}', '').strip()
            if med_id and batch_number and qty and expiry:
                med = Medication.objects.filter(pk=med_id, inventory_item__is_active=True).first()
                if not med:
                    errors.append(f'Line {idx + 1}: medication not found.')
                elif MedicationBatch.objects.filter(medication_id=med_id, batch_number=batch_number).exists():
                    errors.append(f'Line {idx + 1}: batch number "{batch_number}" already exists for {med.name}.')
                else:
                    from datetime import datetime
                    try:
                        expiry_date = datetime.strptime(expiry, '%Y-%m-%d').date()
                    except ValueError:
                        errors.append(f'Line {idx + 1}: invalid expiry date.')
                        idx += 1
                        continue
                    if expiry_date <= date.today():
                        errors.append(f'Line {idx + 1} ({med.name}): expiration date must be in the future.')
                    unit_cost = Decimal(p.get(f'cost_{idx}') or str(med.purchase_price))
                    line_overridden = bool(p.get(f'selling_price_overridden_{idx}'))
                    line_selling_price, line_is_manual, line_computed_price = _resolve_selling_price(
                        unit_cost, p.get(f'selling_price_{idx}'), line_overridden,
                    )
                    lines.append({
                        'med': med,
                        'batch_number': batch_number,
                        'lot_number': p.get(f'lot_number_{idx}', '').strip(),
                        'manufacturing_date': p.get(f'mfg_date_{idx}') or None,
                        'expiration_date': expiry_date,
                        'qty': int(qty),
                        'unit_cost': unit_cost,
                        'selling_price': line_selling_price,
                        'selling_price_is_manual': line_is_manual,
                        'selling_price_computed': line_computed_price,
                        'location_id': p.get(f'location_{idx}') or None,
                    })
            idx += 1

        if not lines and not errors:
            errors.append('Add at least one drug to the receipt.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            with transaction.atomic():
                receipt = GoodsReceipt.objects.create(
                    supplier_id=supplier_id,
                    supplier_invoice_number=invoice_number,
                    supplier_invoice_date=invoice_date,
                    purchase_order_ref=po_ref,
                    delivery_note_number=delivery_note,
                    payment_method=payment_method,
                    received_by=request.user,
                    notes=notes,
                )

                total_cost = sum(l['unit_cost'] * l['qty'] for l in lines)

                if payment_method == GoodsReceipt.PaymentMethod.CASH:
                    receipt.cash_payment_date = date.today()
                    receipt.cash_amount_paid = total_cost
                    receipt.cash_cashier = request.user
                    receipt.cash_payment_reference = p.get('cash_payment_reference', '').strip()
                    receipt.save()
                    ownership = MedicationBatch.Ownership.PURCHASED
                else:
                    due_date = p.get('due_date') or None
                    credit_days = int(p.get('credit_period_days')) if p.get('credit_period_days') else None
                    if not due_date and credit_days:
                        due_date = date.today() + timedelta(days=credit_days)
                    SupplierPayable.objects.create(
                        goods_receipt=receipt,
                        supplier_id=supplier_id,
                        total_amount=total_cost,
                        due_date=due_date,
                        credit_period_days=credit_days,
                        consignment_agreement_number=p.get('consignment_agreement_number', '').strip(),
                    )
                    ownership = (
                        MedicationBatch.Ownership.CONSIGNMENT
                        if payment_method == GoodsReceipt.PaymentMethod.CONSIGNMENT
                        else MedicationBatch.Ownership.PURCHASED
                    )

                created_batches = []
                for l in lines:
                    med = l['med']
                    batch = MedicationBatch.objects.create(
                        medication=med,
                        batch_number=l['batch_number'],
                        lot_number=l['lot_number'],
                        manufacturing_date=l['manufacturing_date'],
                        expiration_date=l['expiration_date'],
                        quantity_received=l['qty'],
                        quantity_available=l['qty'],
                        purchase_price=l['unit_cost'],
                        selling_price=l['selling_price'],
                        selling_price_is_manual=l['selling_price_is_manual'],
                        supplier_id=supplier_id,
                        location_id=l['location_id'],
                        received_date=date.today(),
                        received_by=request.user,
                        purchase_order_ref=po_ref,
                        invoice_number=invoice_number,
                        goods_receipt=receipt,
                        ownership=ownership,
                        status=MedicationBatch.BatchStatus.ACTIVE,
                    )
                    StockTransaction.objects.create(
                        medication=med,
                        batch=batch,
                        transaction_type=StockTransaction.TxType.PURCHASE,
                        quantity_in=l['qty'],
                        quantity_out=0,
                        balance_after=med.current_stock,
                        unit_cost=l['unit_cost'],
                        total_value=l['unit_cost'] * l['qty'],
                        reference_number=_gen_tx_ref(),
                        notes=notes,
                        performed_by=request.user,
                        transaction_date=timezone.now(),
                    )
                    created_batches.append(batch)
                    if l['selling_price_is_manual']:
                        log_action(
                            request.user, AuditLog.Action.ADJUST, AuditLog.Module.MED_INVENTORY,
                            object_type='MedicationBatch', object_id=batch.pk,
                            object_repr=f'{med.name} / {batch.batch_number}',
                            description=(
                                f'Selling price manually overridden for batch {batch.batch_number} ({med.name}): '
                                f"ETB {l['selling_price_computed']} (auto) → ETB {l['selling_price']} (manual)"
                            ),
                            changes={'selling_price': {'old': str(l['selling_price_computed']), 'new': str(l['selling_price'])}},
                            extra_data={'auto_calculated': str(l['selling_price_computed']), 'manual_override': str(l['selling_price'])},
                            request=request,
                        )

                med_names = ', '.join(b.medication.name for b in created_batches)
                log_action(
                    request.user, AuditLog.Action.RECEIVE, AuditLog.Module.MED_INVENTORY,
                    object_type='GoodsReceipt', object_id=receipt.pk,
                    object_repr=receipt.goods_receipt_number,
                    description=f'Goods receipt {receipt.goods_receipt_number} from {receipt.supplier.name}: {len(created_batches)} item(s) — {med_names} ({receipt.get_payment_method_display()})',
                    extra_data={
                        'goods_receipt': receipt.goods_receipt_number,
                        'supplier': receipt.supplier.name,
                        'payment_method': payment_method,
                        'line_count': len(created_batches),
                        'total_cost': str(total_cost),
                    },
                    request=request,
                )
            messages.success(request, f'Goods receipt {receipt.goods_receipt_number} recorded — {len(created_batches)} item(s), ETB {total_cost:,.2f}.')
            return redirect('goods_receipt_detail', receipt_id=receipt.id)

    import json as _json
    return render(request, 'med_inventory/goods_receipt_form.html', {
        'suppliers': suppliers,
        'locations': locations,
        'locations_json': _json.dumps([{'id': loc.id, 'name': str(loc)} for loc in locations]),
        'payment_methods': GoodsReceipt.PaymentMethod.choices,
        'today': date.today().isoformat(),
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
    })


@hms_permission_required('core.receive_stock')
def goods_receipt_detail(request, receipt_id):
    receipt = get_object_or_404(GoodsReceipt.objects.select_related('supplier', 'received_by'), id=receipt_id)
    batches = receipt.batches.select_related('medication').order_by('medication__brand_name')
    payable = getattr(receipt, 'payable', None)
    total_cost = sum((b.purchase_price or 0) * b.quantity_received for b in batches)
    return render(request, 'med_inventory/goods_receipt_detail.html', {
        'receipt': receipt,
        'batches': batches,
        'payable': payable,
        'total_cost': total_cost,
    })


@hms_permission_required('core.receive_stock')
def goods_receipt_print(request, receipt_id):
    """Print-optimised Goods Receiving Note (GRN) view."""
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related('supplier', 'received_by', 'cash_cashier'),
        id=receipt_id,
    )
    batches = receipt.batches.select_related('medication', 'location').order_by('medication__brand_name')
    payable = getattr(receipt, 'payable', None)
    total_cost = sum((b.purchase_price or 0) * b.quantity_received for b in batches)
    total_qty  = sum(b.quantity_received for b in batches)
    return render(request, 'med_inventory/goods_receipt_print.html', {
        'receipt':    receipt,
        'batches':    batches,
        'payable':    payable,
        'total_cost': total_cost,
        'total_qty':  total_qty,
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
    medications = Medication.objects.filter(inventory_item__is_active=True).order_by('brand_name')

    if request.method == 'POST':
        p      = request.POST
        med_id = p.get('medication')
        adj    = int(p.get('adjustment', 0))
        reason = p.get('reason', '').strip()
        med    = get_object_or_404(Medication, id=med_id, inventory_item__is_active=True)

        if adj == 0:
            messages.error(request, 'Adjustment quantity cannot be zero.')
        else:
            # Find the most recent valid batch and adjust it
            batch = med.batches.filter(
                is_active=True, quantity_available__gt=0, status=MedicationBatch.BatchStatus.ACTIVE,
            ).order_by('expiration_date').first()

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


@hms_permission_required('core.manage_suppliers')
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
    empty_post = {'name': '', 'code': '', 'contact_person': '', 'phone': '', 'email': '', 'address': ''}
    return render(request, 'med_inventory/supplier_form.html', {'action': 'Create', 'post': {**empty_post, **request.POST.dict()}})


@hms_permission_required('core.manage_suppliers')
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
    empty_post = {'name': '', 'code': '', 'contact_person': '', 'phone': '', 'email': '', 'address': ''}
    return render(request, 'med_inventory/supplier_form.html', {
        'action': 'Edit', 'sup': sup, 'post': {**empty_post, **request.POST.dict()},
    })


# ── Supplier Payables ──────────────────────────────────────────────────────────

@hms_permission_required('core.view_inv_reports')
def supplier_payable_list(request):
    qs = SupplierPayable.objects.select_related('supplier', 'goods_receipt').all()

    status_f    = request.GET.get('status', '')
    supplier_id = request.GET.get('supplier', '')
    q           = request.GET.get('q', '').strip()

    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    if q:
        qs = qs.filter(
            Q(supplier__name__icontains=q) | Q(goods_receipt__goods_receipt_number__icontains=q)
            | Q(goods_receipt__supplier_invoice_number__icontains=q)
        )

    payables = list(qs)
    if status_f:
        payables = [p for p in payables if p.status == dict(
            unpaid='Unpaid', partial='Partially Paid', paid='Fully Paid', overdue='Overdue',
        ).get(status_f, status_f)]

    total_outstanding = sum(p.outstanding_balance for p in payables)

    paginator = Paginator(payables, 25)
    return render(request, 'med_inventory/supplier_payable_list.html', {
        'page_obj':   paginator.get_page(request.GET.get('page')),
        'suppliers':  Supplier.objects.filter(is_active=True).order_by('name'),
        'status_f':   status_f,
        'supplier_id': supplier_id,
        'q':          q,
        'total_outstanding': total_outstanding,
        'today':      date.today(),
    })


@hms_permission_required('core.view_inv_reports')
def supplier_payable_detail(request, payable_id):
    payable = get_object_or_404(
        SupplierPayable.objects.select_related('supplier', 'goods_receipt'), id=payable_id,
    )
    payments = payable.payments.select_related('recorded_by').order_by('-payment_date')
    batches  = payable.goods_receipt.batches.select_related('medication')
    return render(request, 'med_inventory/supplier_payable_detail.html', {
        'payable':  payable,
        'payments': payments,
        'batches':  batches,
        'today':    date.today(),
    })


@hms_permission_required('core.manage_supplier_payments')
def supplier_payment_record(request, payable_id):
    payable = get_object_or_404(
        SupplierPayable.objects.select_related('supplier', 'goods_receipt'), id=payable_id,
    )
    if request.method == 'POST':
        p = request.POST
        errors = []
        amount_str = p.get('amount', '').strip()

        if not amount_str:
            errors.append('Payment amount is required.')
        else:
            try:
                amount = Decimal(amount_str)
            except Exception:
                errors.append('Invalid payment amount.')
                amount = None
            if amount is not None:
                if amount <= 0:
                    errors.append('Payment amount must be greater than zero.')
                elif amount > payable.outstanding_balance:
                    errors.append(f'Payment (ETB {amount:,.2f}) exceeds outstanding balance (ETB {payable.outstanding_balance:,.2f}).')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            payment = SupplierPayment.objects.create(
                payable=payable,
                amount=amount,
                payment_date=p.get('payment_date') or date.today(),
                payment_reference=p.get('payment_reference', '').strip(),
                recorded_by=request.user,
                notes=p.get('notes', '').strip(),
            )
            payable.refresh_from_db()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.MED_INVENTORY,
                object_type='SupplierPayable', object_id=payable.pk,
                object_repr=f'{payable.supplier.name} — {payable.goods_receipt.goods_receipt_number}',
                description=f'Payment of ETB {amount:,.2f} recorded against {payable.goods_receipt.goods_receipt_number} ({payable.supplier.name}). Status: {payable.status}',
                extra_data={
                    'supplier': payable.supplier.name,
                    'goods_receipt': payable.goods_receipt.goods_receipt_number,
                    'amount': str(amount),
                    'new_status': payable.status,
                },
                request=request,
            )
            messages.success(request, f'Payment of ETB {amount:,.2f} recorded. Status: {payable.status}.')
            return redirect('supplier_payable_detail', payable_id=payable.id)

    return render(request, 'med_inventory/supplier_payment_form.html', {
        'payable': payable,
        'today':   date.today().isoformat(),
    })


@hms_permission_required('core.view_inv_reports')
def report_supplier_performance(request):
    """Supplier Report — purchase value and batch count supplied per vendor."""
    from .models import InventoryBatch

    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    med_batches = MedicationBatch.objects.filter(supplier__isnull=False)
    inv_batches = InventoryBatch.objects.filter(supplier__isnull=False)
    if date_from:
        med_batches = med_batches.filter(received_date__gte=date_from)
        inv_batches = inv_batches.filter(received_date__gte=date_from)
    if date_to:
        med_batches = med_batches.filter(received_date__lte=date_to)
        inv_batches = inv_batches.filter(received_date__lte=date_to)

    rows = []
    for sup in Supplier.objects.filter(is_active=True).order_by('name'):
        med_qs = med_batches.filter(supplier=sup)
        inv_qs = inv_batches.filter(supplier=sup)
        med_value = sum((b.quantity_received or 0) * (b.purchase_price or 0) for b in med_qs)
        inv_value = sum((b.quantity_received or 0) * (b.purchase_price or 0) for b in inv_qs)
        batch_count = med_qs.count() + inv_qs.count()
        if batch_count == 0:
            continue

        receipts = GoodsReceipt.objects.filter(supplier=sup)
        if date_from: receipts = receipts.filter(received_date__date__gte=date_from)
        if date_to:   receipts = receipts.filter(received_date__date__lte=date_to)
        cash_value = consignment_value = Decimal('0')
        for gr in receipts:
            gr_value = sum(b.purchase_price * b.quantity_received for b in gr.batches.all())
            if gr.payment_method == GoodsReceipt.PaymentMethod.CASH:
                cash_value += gr_value
            elif gr.payment_method == GoodsReceipt.PaymentMethod.CONSIGNMENT:
                consignment_value += gr_value
        credit_value = (med_value + inv_value) - cash_value - consignment_value

        rows.append({
            'supplier': sup,
            'batch_count': batch_count,
            'total_value': med_value + inv_value,
            'cash_value': cash_value,
            'credit_value': credit_value,
            'consignment_value': consignment_value,
        })
    rows.sort(key=lambda r: -r['total_value'])
    grand_total = sum(r['total_value'] for r in rows)

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="supplier_report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Supplier', 'Code', 'Contact', 'Phone', 'Batches Supplied',
                          'Cash Value', 'Credit Value', 'Consignment Value', 'Total Purchase Value'])
        for r in rows:
            writer.writerow([r['supplier'].name, r['supplier'].code, r['supplier'].contact_person,
                              r['supplier'].phone, r['batch_count'],
                              r['cash_value'], r['credit_value'], r['consignment_value'], r['total_value']])
        return response

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'med_inventory/reports/supplier_report.html', {
        'rows': rows,
        'grand_total': grand_total,
        'date_from': date_from,
        'date_to': date_to,
        'qp': qp,
    })


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
            Q(medication__brand_name__icontains=q)
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
        'medications': Medication.objects.filter(inventory_item__is_active=True).order_by('brand_name'),
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
        Medication.objects.filter(inventory_item__is_active=True)
        .select_related('inventory_item', 'inventory_item__category', 'inventory_item__supplier', 'inventory_item__storage_location')
        .order_by('brand_name')
    )

    if q:
        medications = [m for m in medications if q.lower() in m.name.lower() or q.lower() in m.generic_name.lower()]
    if category:
        medications = [m for m in medications if m.inventory_item.category_id == int(category)]
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
        'categories':  InventoryCategory.objects.order_by('name'),
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
        'medications':  Medication.objects.filter(inventory_item__is_active=True).order_by('brand_name'),
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
        m for m in Medication.objects.filter(inventory_item__is_active=True)
        .select_related('inventory_item', 'inventory_item__category', 'inventory_item__supplier')
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


def _tx_description(tx):
    """Return a human-readable description of the source/destination for a stock transaction."""
    t = tx.transaction_type
    TxType = StockTransaction.TxType
    if t == TxType.OPENING:
        return 'Opening Balance'
    if t == TxType.PURCHASE:
        sup = tx.batch.supplier.name if (tx.batch and tx.batch.supplier) else 'Supplier'
        return f'Received From: {sup}'
    if t == TxType.DISPENSE:
        if tx.patient:
            return f'Dispensed To: {tx.patient.first_name} {tx.patient.last_name}'.strip()
        return 'Dispensed to Patient'
    if t == TxType.ADJUSTMENT_IN:
        return ('Adjustment In — ' + tx.notes) if tx.notes else 'Adjustment In'
    if t == TxType.ADJUSTMENT_OUT:
        return ('Adjustment Out — ' + tx.notes) if tx.notes else 'Adjustment Out'
    if t == TxType.TRANSFER_IN:
        src = str(tx.source_location) if tx.source_location else 'Store'
        return f'Transfer From: {src}'
    if t == TxType.TRANSFER_OUT:
        dst = str(tx.dest_location) if tx.dest_location else 'Store'
        return f'Transfer To: {dst}'
    if t == TxType.DAMAGE:
        return 'Damage / Loss'
    if t == TxType.EXPIRED_DISPOSAL:
        return 'Expired Disposal'
    if t == TxType.RETURN:
        return 'Return to Stock'
    return tx.get_transaction_type_display()


@hms_permission_required('core.read_medication_inventory')
def report_stock_card(request):
    today     = date.today()
    med_id    = request.GET.get('med', '')
    cat_id    = request.GET.get('category', '')
    batch_no  = request.GET.get('batch', '').strip()
    loc_id    = request.GET.get('location', '')
    date_from = request.GET.get('from', (today - timedelta(days=30)).isoformat())
    date_to   = request.GET.get('to', today.isoformat())

    med         = get_object_or_404(Medication, id=med_id) if med_id else None
    medications = Medication.objects.filter(inventory_item__is_active=True).order_by('brand_name')
    categories  = InventoryCategory.objects.all().order_by('name')
    locations   = StorageLocation.objects.all().order_by('name')
    transactions = []
    has_filter  = bool(med_id or cat_id or batch_no or loc_id)

    if has_filter:
        qs = (StockTransaction.objects
              .select_related(
                  'medication', 'medication__inventory_item', 'medication__inventory_item__category',
                  'batch', 'batch__supplier', 'batch__location',
                  'performed_by', 'patient',
                  'source_location', 'dest_location',
              )
              .order_by('medication__brand_name', 'transaction_date'))

        if med_id:   qs = qs.filter(medication_id=med_id)
        if cat_id:   qs = qs.filter(medication__inventory_item__category_id=cat_id)
        if batch_no: qs = qs.filter(batch__batch_number__icontains=batch_no)
        if loc_id:
            qs = qs.filter(
                Q(source_location_id=loc_id) |
                Q(dest_location_id=loc_id) |
                Q(batch__location_id=loc_id)
            )
        if date_from: qs = qs.filter(transaction_date__date__gte=date_from)
        if date_to:   qs = qs.filter(transaction_date__date__lte=date_to)

        transactions = list(qs)
        for tx in transactions:
            tx.calc_description = _tx_description(tx)
            area = tx.source_location or tx.dest_location
            if not area and tx.batch and tx.batch.location:
                area = tx.batch.location
            tx.calc_area = str(area) if area else '—'

        export = request.GET.get('export', '')
        if export == 'csv':
            return _export_stock_card_csv(med, transactions)
        if export == 'excel':
            label = med.name if med else ('Category: ' + categories.get(pk=cat_id).name if cat_id else 'All Items')
            return _export_stock_card_excel(transactions, label)

    return render(request, 'med_inventory/reports/stock_card.html', {
        'med':           med,
        'medications':   medications,
        'categories':    categories,
        'locations':     locations,
        'transactions':  transactions,
        'date_from':     date_from,
        'date_to':       date_to,
        'cat_id':        cat_id,
        'batch_no':      batch_no,
        'loc_id':        loc_id,
        'has_filter':    has_filter,
        'multi_item':    not bool(med_id),
        'generated_at':  timezone.now(),
        'generated_by':  request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_valuation(request):
    medications = list(
        Medication.objects.filter(inventory_item__is_active=True)
        .select_related('inventory_item', 'inventory_item__category')
        .order_by('brand_name')
    )
    for m in medications:
        m.calc_stock   = m.current_stock
        m.calc_value   = m.inventory_value
        m.calc_expired_qty   = sum(b.quantity_available for b in m.expired_batches)
        m.calc_expired_value = m.purchase_price * m.calc_expired_qty
        consignment_batches = m.batches.filter(
            is_active=True, quantity_available__gt=0, ownership=MedicationBatch.Ownership.CONSIGNMENT,
        )
        m.calc_consignment_qty   = sum(b.quantity_available for b in consignment_batches)
        m.calc_consignment_value = m.purchase_price * m.calc_consignment_qty
        m.calc_purchased_value   = m.calc_value - m.calc_consignment_value

    total_value        = sum(m.calc_value for m in medications)
    expired_value       = sum(m.calc_expired_value for m in medications)
    consignment_value   = sum(m.calc_consignment_value for m in medications)
    purchased_value      = total_value - consignment_value

    if request.GET.get('export') == 'csv':
        return _export_valuation_csv(medications, total_value, expired_value)
    if request.GET.get('export') == 'excel':
        return _export_valuation_excel(medications, total_value, expired_value)

    return render(request, 'med_inventory/reports/valuation.html', {
        'medications':   medications,
        'total_value':   total_value,
        'expired_value': expired_value,
        'purchased_value':   purchased_value,
        'consignment_value': consignment_value,
        'generated_at':  timezone.now(),
        'generated_by':  request.user,
    })


# ── Receiving / Accounts Payable reports ───────────────────────────────────────

def _filtered_goods_receipts(request, fixed_payment_method=None):
    qs = GoodsReceipt.objects.select_related('supplier', 'received_by').prefetch_related('batches__medication')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    supplier_id = request.GET.get('supplier', '')
    payment_method = fixed_payment_method or request.GET.get('payment_method', '')

    if date_from:      qs = qs.filter(received_date__date__gte=date_from)
    if date_to:        qs = qs.filter(received_date__date__lte=date_to)
    if supplier_id:    qs = qs.filter(supplier_id=supplier_id)
    if payment_method: qs = qs.filter(payment_method=payment_method)

    receipts = list(qs.order_by('-received_date'))
    for r in receipts:
        r.calc_line_count  = len(r.batches.all())
        r.calc_total_value = sum(b.purchase_price * b.quantity_received for b in r.batches.all())
    return receipts, date_from, date_to, supplier_id, payment_method


def _export_receiving_csv(receipts, filename, grand_total):
    resp = _csv_response(filename)
    w = csv.writer(resp)
    w.writerow(['Receipt #', 'Date', 'Supplier', 'Invoice #', 'Invoice Date',
                'Payment Method', 'Line Items', 'Total Value (ETB)', 'Received By'])
    for r in receipts:
        w.writerow([
            r.goods_receipt_number, r.received_date.strftime('%Y-%m-%d'), r.supplier.name,
            r.supplier_invoice_number, r.supplier_invoice_date or '',
            r.get_payment_method_display(), r.calc_line_count, r.calc_total_value,
            r.received_by.get_full_name() or r.received_by.username,
        ])
    w.writerow([])
    w.writerow(['', '', '', '', '', '', 'TOTAL:', grand_total, ''])
    return resp


def _render_receiving_report(request, template, title, fixed_payment_method=None, filename_prefix='stock_receiving'):
    receipts, date_from, date_to, supplier_id, payment_method = _filtered_goods_receipts(request, fixed_payment_method)
    grand_total = sum(r.calc_total_value for r in receipts)

    if request.GET.get('export') == 'csv':
        return _export_receiving_csv(receipts, f'{filename_prefix}_{date.today()}.csv', grand_total)

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, template, {
        'title':          title,
        'receipts':       receipts,
        'grand_total':    grand_total,
        'suppliers':      Supplier.objects.filter(is_active=True).order_by('name'),
        'payment_methods': GoodsReceipt.PaymentMethod.choices,
        'date_from':      date_from,
        'date_to':        date_to,
        'supplier_id':    supplier_id,
        'payment_method': payment_method,
        'fixed_payment_method': fixed_payment_method,
        'qp':             qp,
        'generated_at':   timezone.now(),
        'generated_by':   request.user,
    })


@hms_permission_required('core.view_inv_reports')
def report_stock_receiving(request):
    """Every Goods Receipt in a period, filterable by supplier/date/payment method."""
    return _render_receiving_report(
        request, 'med_inventory/reports/stock_receiving.html', 'Stock Receiving Report',
        filename_prefix='stock_receiving',
    )


@hms_permission_required('core.view_inv_reports')
def report_cash_purchases(request):
    return _render_receiving_report(
        request, 'med_inventory/reports/stock_receiving.html', 'Cash Purchases Report',
        fixed_payment_method=GoodsReceipt.PaymentMethod.CASH, filename_prefix='cash_purchases',
    )


@hms_permission_required('core.view_inv_reports')
def report_credit_purchases(request):
    return _render_receiving_report(
        request, 'med_inventory/reports/stock_receiving.html', 'Credit Purchases Report',
        fixed_payment_method=GoodsReceipt.PaymentMethod.CREDIT, filename_prefix='credit_purchases',
    )


@hms_permission_required('core.view_inv_reports')
def report_consignment_purchases(request):
    return _render_receiving_report(
        request, 'med_inventory/reports/stock_receiving.html', 'Consignment Purchases Report',
        fixed_payment_method=GoodsReceipt.PaymentMethod.CONSIGNMENT, filename_prefix='consignment_purchases',
    )


def _payables_csv(payables, filename):
    resp = _csv_response(filename)
    w = csv.writer(resp)
    w.writerow(['Supplier', 'Receipt #', 'Invoice #', 'Total (ETB)', 'Paid (ETB)',
                'Outstanding (ETB)', 'Due Date', 'Status'])
    for p in payables:
        w.writerow([
            p.supplier.name, p.goods_receipt.goods_receipt_number, p.goods_receipt.supplier_invoice_number,
            p.total_amount, p.amount_paid, p.outstanding_balance, p.due_date or '', p.status,
        ])
    return resp


@hms_permission_required('core.view_inv_reports')
def report_outstanding_credit(request):
    """Every open (not fully paid) Credit/Consignment payable."""
    qs = SupplierPayable.objects.select_related('supplier', 'goods_receipt').filter(
        amount_paid__lt=F('total_amount'),
    )
    supplier_id = request.GET.get('supplier', '')
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    payables = list(qs.order_by('due_date'))
    grand_total = sum(p.outstanding_balance for p in payables)

    if request.GET.get('export') == 'csv':
        return _payables_csv(payables, f'outstanding_credit_{date.today()}.csv')

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'med_inventory/reports/outstanding_credit.html', {
        'title':       'Outstanding Credit Report',
        'payables':    payables,
        'grand_total': grand_total,
        'suppliers':   Supplier.objects.filter(is_active=True).order_by('name'),
        'supplier_id': supplier_id,
        'today':       date.today(),
        'qp':          qp,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.view_inv_reports')
def report_credit_due(request):
    """Payables due within the configurable reminder window (not yet overdue)."""
    today = date.today()
    reminder_days = PharmacyCreditSettings.get_solo().reminder_days_before_due
    limit = today + timedelta(days=reminder_days)

    qs = SupplierPayable.objects.select_related('supplier', 'goods_receipt').filter(
        amount_paid__lt=F('total_amount'), due_date__gte=today, due_date__lte=limit,
    )
    payables = list(qs.order_by('due_date'))
    grand_total = sum(p.outstanding_balance for p in payables)

    if request.GET.get('export') == 'csv':
        return _payables_csv(payables, f'credit_due_{date.today()}.csv')

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'med_inventory/reports/outstanding_credit.html', {
        'title':       'Credit Due Report',
        'payables':    payables,
        'grand_total': grand_total,
        'suppliers':   Supplier.objects.filter(is_active=True).order_by('name'),
        'supplier_id': '',
        'today':       today,
        'reminder_days': reminder_days,
        'qp':          qp,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.view_inv_reports')
def report_overdue_supplier_payments(request):
    """Payables past their due date and still unpaid."""
    today = date.today()
    qs = SupplierPayable.objects.select_related('supplier', 'goods_receipt').filter(
        amount_paid__lt=F('total_amount'), due_date__lt=today,
    )
    payables = list(qs.order_by('due_date'))
    grand_total = sum(p.outstanding_balance for p in payables)

    if request.GET.get('export') == 'csv':
        return _payables_csv(payables, f'overdue_supplier_payments_{date.today()}.csv')

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'med_inventory/reports/outstanding_credit.html', {
        'title':       'Overdue Supplier Payments Report',
        'payables':    payables,
        'grand_total': grand_total,
        'suppliers':   Supplier.objects.filter(is_active=True).order_by('name'),
        'supplier_id': '',
        'today':       today,
        'qp':          qp,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.view_inv_reports')
def report_supplier_invoice_register(request):
    """Every supplier invoice number recorded, flagging any invoice number
    reused across more than one receipt (a common data-entry mistake)."""
    supplier_id = request.GET.get('supplier', '')
    qs = GoodsReceipt.objects.select_related('supplier').order_by('supplier__name', '-received_date')
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    receipts = list(qs)

    invoice_counts = {}
    for r in receipts:
        invoice_counts[r.supplier_invoice_number] = invoice_counts.get(r.supplier_invoice_number, 0) + 1
    for r in receipts:
        r.calc_is_duplicate_invoice = invoice_counts[r.supplier_invoice_number] > 1

    if request.GET.get('export') == 'csv':
        resp = _csv_response(f'supplier_invoice_register_{date.today()}.csv')
        w = csv.writer(resp)
        w.writerow(['Supplier', 'Invoice #', 'Invoice Date', 'Receipt #', 'Received Date', 'Payment Method', 'Possible Duplicate'])
        for r in receipts:
            w.writerow([r.supplier.name, r.supplier_invoice_number, r.supplier_invoice_date or '',
                        r.goods_receipt_number, r.received_date.strftime('%Y-%m-%d'),
                        r.get_payment_method_display(), 'YES' if r.calc_is_duplicate_invoice else ''])
        return resp

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'med_inventory/reports/supplier_invoice_register.html', {
        'receipts':    receipts,
        'suppliers':   Supplier.objects.filter(is_active=True).order_by('name'),
        'supplier_id': supplier_id,
        'qp':          qp,
        'generated_at': timezone.now(),
        'generated_by': request.user,
    })


@hms_permission_required('core.read_medication_inventory')
def report_batch_tracking(request):
    """A single batch's full lifecycle: received → transactions → disposed."""
    batch_id = request.GET.get('batch', '')
    q        = request.GET.get('q', '').strip()

    batch = None
    transactions = []
    if batch_id:
        batch = get_object_or_404(
            MedicationBatch.objects.select_related('medication', 'supplier', 'goods_receipt', 'location'),
            id=batch_id,
        )
        transactions = list(batch.transactions.select_related('performed_by', 'patient').order_by('transaction_date'))

    candidates = []
    if q and not batch:
        candidates = list(
            MedicationBatch.objects.filter(
                Q(batch_number__icontains=q) | Q(medication__brand_name__icontains=q)
            ).select_related('medication').order_by('-created_at')[:20]
        )

    if batch and request.GET.get('export') == 'csv':
        resp = _csv_response(f'batch_tracking_{batch.batch_number}_{date.today()}.csv')
        w = csv.writer(resp)
        w.writerow([f'Batch Tracking — {batch.medication.name} / {batch.batch_number}'])
        w.writerow([])
        w.writerow(['Date', 'Transaction Type', 'Qty In', 'Qty Out', 'Balance', 'Unit Cost', 'Reference', 'Performed By', 'Notes'])
        for tx in transactions:
            w.writerow([
                tx.transaction_date.strftime('%Y-%m-%d %H:%M'), tx.get_transaction_type_display(),
                tx.quantity_in, tx.quantity_out, tx.balance_after, tx.unit_cost,
                tx.reference_number, tx.performed_by.get_full_name() or tx.performed_by.username, tx.notes,
            ])
        return resp

    return render(request, 'med_inventory/reports/batch_tracking.html', {
        'batch':        batch,
        'transactions': transactions,
        'candidates':   candidates,
        'q':            q,
        'generated_at': timezone.now(),
        'generated_by': request.user,
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
            m.calc_stock, m.unit_of_measure,
            m.purchase_price, m.calc_value,
            m.calc_expired_qty, m.calc_expired_value,
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
            m.calc_stock, m.unit_of_measure,
            float(m.purchase_price), float(m.calc_value),
            m.calc_expired_qty, float(m.calc_expired_value),
        ])
    ws.append([])
    ws.append(['', '', '', '', 'TOTAL:', '', '', float(total_value), '', float(expired_value)])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 20
    resp = _excel_response(f'inventory_valuation_{date.today()}.xlsx')
    wb.save(resp)
    return resp


def _export_stock_card_csv(med, transactions):
    label = f'{med.code}' if med else 'all'
    resp = _csv_response(f'stock_card_{label}_{date.today()}.csv')
    w = csv.writer(resp)
    title = f'Stock Card — {med.name} ({med.generic_name}) — {med.code}' if med else 'Stock Card — All Items'
    w.writerow([title])
    w.writerow([])
    w.writerow(['Date', 'Item Code', 'Item Name', 'Batch #', 'Expiry Date',
                'Transaction Type', 'Description',
                'Qty In', 'Qty Out', 'Balance',
                'Unit Cost (ETB)', 'Total Value (ETB)',
                'Location/Area', 'Reference #', 'Performed By', 'Notes'])
    for tx in transactions:
        w.writerow([
            tx.transaction_date.strftime('%Y-%m-%d %H:%M') if tx.transaction_date else '',
            getattr(tx.medication, 'code', ''),
            tx.medication.name,
            tx.batch.batch_number if tx.batch else '',
            str(tx.batch.expiration_date) if tx.batch else '',
            tx.get_transaction_type_display(),
            getattr(tx, 'calc_description', ''),
            tx.quantity_in or '', tx.quantity_out or '', tx.balance_after,
            tx.unit_cost, tx.total_value,
            getattr(tx, 'calc_area', ''),
            tx.reference_number,
            tx.performed_by.get_full_name() or tx.performed_by.username,
            tx.notes,
        ])
    return resp


def _export_stock_card_excel(transactions, title):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Stock Card'
    ws.append([f'Stock Card — {title} — Generated: {date.today()}'])
    ws.append([])
    headers = ['Date', 'Item Code', 'Item Name', 'Batch #', 'Expiry Date',
               'Transaction Type', 'Description',
               'Qty In', 'Qty Out', 'Balance',
               'Unit Cost (ETB)', 'Total Value (ETB)',
               'Location / Area', 'Reference #', 'Performed By']
    ws.append(headers)
    for cell in ws[3]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1E40AF')
    for tx in transactions:
        ws.append([
            tx.transaction_date.strftime('%Y-%m-%d %H:%M') if tx.transaction_date else '',
            getattr(tx.medication, 'code', ''),
            tx.medication.name,
            tx.batch.batch_number if tx.batch else '',
            str(tx.batch.expiration_date) if tx.batch else '',
            tx.get_transaction_type_display(),
            getattr(tx, 'calc_description', ''),
            tx.quantity_in or None,
            tx.quantity_out or None,
            tx.balance_after,
            float(tx.unit_cost),
            float(tx.total_value),
            getattr(tx, 'calc_area', ''),
            tx.reference_number,
            tx.performed_by.get_full_name() or tx.performed_by.username,
        ])
    col_widths = [18, 12, 22, 14, 12, 20, 35, 8, 8, 10, 14, 14, 22, 14, 20]
    for i, col in enumerate(ws.columns):
        ws.column_dimensions[col[0].column_letter].width = col_widths[i] if i < len(col_widths) else 15
    resp = _excel_response(f'stock_card_{date.today()}.xlsx')
    wb.save(resp)
    return resp
