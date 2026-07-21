from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from decimal import Decimal

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, InventoryCategory, InventoryItem, InventoryTransaction, ItemGroup,
    Medication, PricingSettings, PurchaseOrder, PurchaseOrderItem, UnitOfMeasure,
)


def _resolve_selling_price(purchase_price, submitted_selling_price, is_overridden):
    """Applies the global default markup (Purchase/Unit Cost + markup%)
    unless the user explicitly overrode the Selling Price field. Mirrors
    views_med_inventory._resolve_selling_price for the Inventory module.
    Returns (final_price, is_manual, computed_price)."""
    computed = PricingSettings.get_solo().compute_selling_price(purchase_price)
    if is_overridden:
        try:
            final = Decimal(submitted_selling_price)
        except Exception:
            final = computed
        return final, True, computed
    return computed, False, computed


# ── Inventory Dashboard ───────────────────────────────────────────────────────

@hms_permission_required('core.read_inventory')
def inventory_dashboard(request):
    total_items = InventoryItem.objects.count()
    low_stock_items = [item for item in InventoryItem.objects.all() if item.is_low_stock]
    low_stock_count = len(low_stock_items)
    total_categories = InventoryCategory.objects.count()
    pending_pos = PurchaseOrder.objects.filter(
        status__in=[PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SUBMITTED, PurchaseOrder.Status.APPROVED]
    ).count()
    recent_pos = (
        PurchaseOrder.objects
        .select_related('created_by')
        .order_by('-ordered_at')[:8]
    )
    context = {
        'total_items': total_items,
        'low_stock_count': low_stock_count,
        'total_categories': total_categories,
        'pending_pos': pending_pos,
        'low_stock_items': low_stock_items[:10],
        'recent_pos': recent_pos,
    }
    return render(request, 'inventory/dashboard.html', context)


# ── Inventory Item List ───────────────────────────────────────────────────────

@hms_permission_required('core.read_inventory')
def inventory_item_list(request):
    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    item_group_id = request.GET.get('item_group', '').strip()
    item_type = request.GET.get('item_type', '').strip()

    items = InventoryItem.objects.select_related('category', 'item_group').order_by('name')

    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(generic_name__icontains=query)
            | Q(item_code__icontains=query)
            | Q(sku__icontains=query)
            | Q(barcode__icontains=query)
            | Q(supplier_name__icontains=query)
        )
    if category_id:
        items = items.filter(category_id=category_id)
    if item_group_id:
        items = items.filter(item_group_id=item_group_id)
    if item_type:
        items = items.filter(item_type=item_type)

    categories = InventoryCategory.objects.all()
    item_groups = ItemGroup.objects.filter(is_active=True)
    paginator = Paginator(items, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'inventory/item_list.html', {
        'page_obj': page_obj,
        'query': query,
        'category_id': category_id,
        'categories': categories,
        'item_group_id': item_group_id,
        'item_groups': item_groups,
        'item_type': item_type,
        'item_types': InventoryItem.ItemType.choices,
    })


# ── Inventory Item Create ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_inventory')
def inventory_item_create(request):
    categories = InventoryCategory.objects.all()
    item_groups = ItemGroup.objects.filter(is_active=True)
    units = UnitOfMeasure.objects.filter(is_active=True)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Item name is required.')
        else:
            category_id = request.POST.get('category') or None
            unit_cost = Decimal(request.POST.get('unit_cost') or '0')
            is_overridden = bool(request.POST.get('selling_price_overridden'))
            selling_price, is_manual, computed_price = _resolve_selling_price(
                unit_cost, request.POST.get('selling_price'), is_overridden,
            )
            inv_item = InventoryItem.objects.create(
                name=name,
                item_code=request.POST.get('item_code', '').strip() or None,
                generic_name=request.POST.get('generic_name', '').strip(),
                item_type=request.POST.get('item_type', InventoryItem.ItemType.GENERAL),
                category_id=category_id,
                item_group_id=request.POST.get('item_group') or None,
                subcategory=request.POST.get('subcategory', '').strip(),
                dosage_form=request.POST.get('dosage_form', '').strip(),
                strength=request.POST.get('strength', '').strip(),
                sku=request.POST.get('sku', '').strip(),
                unit=request.POST.get('unit', 'units').strip() or 'units',
                unit_purchase=request.POST.get('unit_purchase', '').strip(),
                dispensing_unit=request.POST.get('dispensing_unit', '').strip(),
                quantity_in_stock=int(request.POST.get('quantity_in_stock', 0) or 0),
                unit_cost=unit_cost,
                selling_price=selling_price,
                selling_price_is_manual=is_manual,
                inpatient_price=request.POST.get('inpatient_price', 0) or 0,
                tax_type=request.POST.get('tax_type', '').strip(),
                reorder_level=int(request.POST.get('reorder_level', 10) or 10),
                min_stock=request.POST.get('min_stock', 0) or 0,
                max_stock=request.POST.get('max_stock') or None,
                supplier_name=request.POST.get('supplier_name', '').strip(),
                is_billable=bool(request.POST.get('is_billable')),
                is_active=bool(request.POST.get('is_active')),
                notes=request.POST.get('notes', '').strip(),
            )
            if inv_item.quantity_in_stock > 0:
                InventoryTransaction.objects.create(
                    inventory_item=inv_item,
                    transaction_type=InventoryTransaction.TxType.ADJUSTMENT_IN,
                    quantity_in=inv_item.quantity_in_stock,
                    balance_after=inv_item.quantity_in_stock,
                    unit_cost=inv_item.unit_cost,
                    reference_number=f'OPEN-{inv_item.pk}',
                    notes='Opening balance at item creation.',
                    performed_by=request.user,
                )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                object_type='InventoryItem', object_id=inv_item.pk, object_repr=inv_item.name,
                description=f'Inventory item "{inv_item.name}" created — qty {inv_item.quantity_in_stock}',
                request=request,
            )
            if is_manual:
                log_action(
                    request.user, AuditLog.Action.ADJUST, AuditLog.Module.INVENTORY,
                    object_type='InventoryItem', object_id=inv_item.pk, object_repr=inv_item.name,
                    description=(
                        f'Selling price manually overridden for {inv_item.name}: '
                        f'ETB {computed_price} (auto) → ETB {selling_price} (manual)'
                    ),
                    changes={'selling_price': {'old': str(computed_price), 'new': str(selling_price)}},
                    extra_data={'auto_calculated': str(computed_price), 'manual_override': str(selling_price)},
                    request=request,
                )
            if inv_item.item_type == InventoryItem.ItemType.MEDICATION:
                messages.success(
                    request,
                    f'Inventory item "{inv_item.name}" created. Complete its Medication Details '
                    f'below to make it available for prescribing/dispensing.',
                )
                return redirect('medication_details_complete', item_id=inv_item.id)
            messages.success(request, 'Inventory item created successfully.')
            return redirect('inventory_item_list')
    return render(request, 'inventory/item_form.html', {
        'categories': categories,
        'item_groups': item_groups,
        'units': units,
        'item_types': InventoryItem.ItemType.choices,
        'action': 'Create',
        'item': None,
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
    })


# ── Inventory Item Edit ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_inventory')
def inventory_item_edit(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    categories = InventoryCategory.objects.all()
    item_groups = ItemGroup.objects.filter(is_active=True)
    units = UnitOfMeasure.objects.filter(is_active=True)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Item name is required.')
        else:
            old_selling_price = item.selling_price
            unit_cost = Decimal(request.POST.get('unit_cost') or '0')
            is_overridden = bool(request.POST.get('selling_price_overridden'))
            selling_price, is_manual, computed_price = _resolve_selling_price(
                unit_cost, request.POST.get('selling_price'), is_overridden,
            )

            item.name = name
            item.item_code = request.POST.get('item_code', '').strip() or None
            item.generic_name = request.POST.get('generic_name', '').strip()
            item.item_type = request.POST.get('item_type', item.item_type)
            item.category_id = request.POST.get('category') or None
            item.item_group_id = request.POST.get('item_group') or None
            item.subcategory = request.POST.get('subcategory', '').strip()
            item.dosage_form = request.POST.get('dosage_form', '').strip()
            item.strength = request.POST.get('strength', '').strip()
            item.sku = request.POST.get('sku', '').strip()
            item.unit = request.POST.get('unit', 'units').strip() or 'units'
            item.unit_purchase = request.POST.get('unit_purchase', '').strip()
            item.dispensing_unit = request.POST.get('dispensing_unit', '').strip()
            item.unit_cost = unit_cost
            item.selling_price = selling_price
            item.selling_price_is_manual = is_manual
            item.inpatient_price = request.POST.get('inpatient_price', 0) or 0
            item.tax_type = request.POST.get('tax_type', '').strip()
            item.reorder_level = int(request.POST.get('reorder_level', 10) or 10)
            item.min_stock = request.POST.get('min_stock', 0) or 0
            item.max_stock = request.POST.get('max_stock') or None
            item.supplier_name = request.POST.get('supplier_name', '').strip()
            item.is_billable = bool(request.POST.get('is_billable'))
            item.is_active = bool(request.POST.get('is_active'))
            item.notes = request.POST.get('notes', '').strip()
            item.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.INVENTORY,
                object_type='InventoryItem', object_id=item.pk, object_repr=item.name,
                description=f'Inventory item "{item.name}" updated', request=request,
            )
            if is_manual and str(selling_price) != str(old_selling_price):
                log_action(
                    request.user, AuditLog.Action.ADJUST, AuditLog.Module.INVENTORY,
                    object_type='InventoryItem', object_id=item.pk, object_repr=item.name,
                    description=(
                        f'Selling price manually overridden for {item.name}: '
                        f'ETB {old_selling_price} → ETB {selling_price} (auto would be ETB {computed_price})'
                    ),
                    changes={'selling_price': {'old': str(old_selling_price), 'new': str(selling_price)}},
                    extra_data={'auto_calculated': str(computed_price), 'manual_override': str(selling_price)},
                    request=request,
                )
            if item.item_type == InventoryItem.ItemType.MEDICATION and not Medication.objects.filter(inventory_item=item).exists():
                messages.success(
                    request,
                    f'Item "{item.name}" updated. It is now typed as Medication — complete its '
                    f'Medication Details below to make it available for prescribing/dispensing.',
                )
                return redirect('medication_details_complete', item_id=item.id)
            messages.success(request, 'Item updated successfully.')
            return redirect('inventory_item_list')
    return render(request, 'inventory/item_form.html', {
        'item': item,
        'categories': categories,
        'item_groups': item_groups,
        'units': units,
        'item_types': InventoryItem.ItemType.choices,
        'action': 'Edit',
        'default_markup_percent': PricingSettings.get_solo().default_markup_percent,
    })


# ── Inventory Item Adjust Stock ───────────────────────────────────────────────

@hms_permission_required('core.manage_inventory')
@require_POST
def inventory_item_adjust(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    adjustment_str = request.POST.get('adjustment', '').strip()
    reason = request.POST.get('reason', '').strip()
    try:
        adjustment = int(adjustment_str)
    except (ValueError, TypeError):
        messages.error(request, 'Invalid adjustment value. Enter a whole number.')
        return redirect('inventory_item_list')

    new_qty = item.quantity_in_stock + adjustment
    if new_qty < 0:
        messages.error(request, f'Adjustment would result in negative stock ({new_qty}). Aborted.')
        return redirect('inventory_item_list')

    old_qty = item.quantity_in_stock
    item.quantity_in_stock = new_qty
    item.save()
    direction = 'Added' if adjustment >= 0 else 'Issued'
    InventoryTransaction.objects.create(
        inventory_item=item,
        transaction_type=(
            InventoryTransaction.TxType.ADJUSTMENT_IN if adjustment >= 0
            else InventoryTransaction.TxType.ADJUSTMENT_OUT
        ),
        quantity_in=adjustment if adjustment >= 0 else 0,
        quantity_out=-adjustment if adjustment < 0 else 0,
        balance_after=new_qty,
        unit_cost=item.unit_cost,
        reference_number=f'ADJ-{item.pk}',
        notes=reason or 'Manual stock adjustment',
        performed_by=request.user,
    )
    log_action(
        request.user, AuditLog.Action.ADJUST, AuditLog.Module.INVENTORY,
        object_type='InventoryItem', object_id=item.pk, object_repr=item.name,
        description=f'{direction} {abs(adjustment)} {item.unit} of "{item.name}". Reason: {reason or "—"}',
        changes={'quantity_in_stock': {'old': str(old_qty), 'new': str(new_qty), 'diff': str(adjustment)}},
        extra_data={'reason': reason},
        request=request,
    )
    messages.success(
        request,
        f'{direction} {abs(adjustment)} {item.unit} of "{item.name}". '
        f'New stock: {new_qty}. Reason: {reason or "—"}'
    )
    return redirect('inventory_item_list')


# ── Item Groups (admin-configurable) ───────────────────────────────────────────

@hms_permission_required('core.read_inventory')
def item_group_list(request):
    groups = ItemGroup.objects.annotate(item_count=Count('items')).order_by('name')
    return render(request, 'inventory/item_group_list.html', {'groups': groups})


@hms_permission_required('core.manage_inventory')
def item_group_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Group name is required.')
        else:
            group = ItemGroup.objects.create(
                name=name, description=request.POST.get('description', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                object_type='ItemGroup', object_id=group.pk, object_repr=group.name,
                description=f'Item group "{group.name}" created', request=request,
            )
            messages.success(request, f'Item group "{group.name}" created.')
            return redirect('item_group_list')
    return render(request, 'inventory/item_group_form.html', {'action': 'Create'})


@hms_permission_required('core.manage_inventory')
def item_group_edit(request, group_id):
    group = get_object_or_404(ItemGroup, pk=group_id)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Group name is required.')
        else:
            group.name = name
            group.description = request.POST.get('description', '').strip()
            group.is_active = bool(request.POST.get('is_active'))
            group.save()
            messages.success(request, f'Item group "{group.name}" updated.')
            return redirect('item_group_list')
    return render(request, 'inventory/item_group_form.html', {'group': group, 'action': 'Edit'})


# ── Units of Measure (admin-configurable) ──────────────────────────────────────

@hms_permission_required('core.read_inventory')
def unit_of_measure_list(request):
    units = UnitOfMeasure.objects.all().order_by('name')
    return render(request, 'inventory/unit_list.html', {'units': units})


@hms_permission_required('core.manage_inventory')
def unit_of_measure_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Unit name is required.')
        elif UnitOfMeasure.objects.filter(name__iexact=name).exists():
            messages.error(request, f'Unit "{name}" already exists.')
        else:
            UnitOfMeasure.objects.create(name=name)
            messages.success(request, f'Unit "{name}" created.')
            return redirect('unit_of_measure_list')
    return render(request, 'inventory/unit_form.html', {'action': 'Create'})


@require_POST
@hms_permission_required('core.manage_inventory')
def unit_of_measure_toggle(request, unit_id):
    unit = get_object_or_404(UnitOfMeasure, pk=unit_id)
    unit.is_active = not unit.is_active
    unit.save(update_fields=['is_active'])
    messages.success(request, f'Unit "{unit.name}" {"activated" if unit.is_active else "deactivated"}.')
    return redirect('unit_of_measure_list')


# ── Item Master List Report ─────────────────────────────────────────────────────

@hms_permission_required('core.view_store_reports')
def report_inventory_item_master(request):
    """Item Master List — full catalog dump (not a stock-state report), with
    category/item-group/type/active filters, matching report_purchase_orders'
    local export convention in this same file."""
    items = InventoryItem.objects.select_related('category', 'item_group').order_by('item_type', 'name')

    category_f = request.GET.get('category', '')
    item_group_f = request.GET.get('item_group', '')
    item_type_f = request.GET.get('item_type', '')
    active_f = request.GET.get('active', '')

    if category_f:
        items = items.filter(category_id=category_f)
    if item_group_f:
        items = items.filter(item_group_id=item_group_f)
    if item_type_f:
        items = items.filter(item_type=item_type_f)
    if active_f == '1':
        items = items.filter(is_active=True)
    elif active_f == '0':
        items = items.filter(is_active=False)

    headers = [
        'Item Code', 'Name', 'Generic Name', 'Category', 'Subcategory', 'Item Group',
        'Item Type', 'Unit', 'Purchase Unit', 'Dispensing Unit', 'Unit Cost',
        'Selling Price', 'Inpatient Price', 'Reorder Level', 'Current Stock', 'Active',
    ]
    rows = [
        [
            it.item_code or it.sku, it.name, it.generic_name,
            it.category.name if it.category else '', it.subcategory,
            it.item_group.name if it.item_group else '', it.item_type,
            it.unit, it.unit_purchase, it.dispensing_unit, it.unit_cost,
            it.selling_price, it.inpatient_price, it.reorder_level,
            it.quantity_in_stock, 'Yes' if it.is_active else 'No',
        ]
        for it in items
    ]

    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="inventory_item_master.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response

    if request.GET.get('export') == 'excel':
        from .report_export import export_excel
        return export_excel('inventory_item_master.xlsx', headers, rows, title='Inventory Item Master List')

    paginator = Paginator(items, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))

    return render(request, 'inventory/reports/item_master.html', {
        'page_obj': page_obj,
        'total_count': items.count(),
        'categories': InventoryCategory.objects.all(),
        'item_groups': ItemGroup.objects.filter(is_active=True),
        'item_types': InventoryItem.ItemType.choices,
        'category_f': category_f, 'item_group_f': item_group_f,
        'item_type_f': item_type_f, 'active_f': active_f,
        'qp': qp,
    })


# ── Purchase Order List ───────────────────────────────────────────────────────

@hms_permission_required('core.read_inventory')
def purchase_order_list(request):
    status_filter = request.GET.get('status', '').strip()
    pos = PurchaseOrder.objects.select_related('created_by', 'approved_by').order_by('-ordered_at')
    if status_filter:
        pos = pos.filter(status=status_filter)

    # annotate item count via prefetch workaround
    paginator = Paginator(pos, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'inventory/po_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'status_choices': PurchaseOrder.Status.choices,
    })


# ── Purchase Order Create ─────────────────────────────────────────────────────

@hms_permission_required('core.create_purchase_order')
def purchase_order_create(request):
    if request.method == 'POST':
        supplier_name = request.POST.get('supplier_name', '').strip()
        if not supplier_name:
            messages.error(request, 'Supplier name is required.')
        else:
            po = PurchaseOrder.objects.create(
                supplier_name=supplier_name,
                supplier_contact=request.POST.get('supplier_contact', '').strip(),
                expected_delivery=request.POST.get('expected_delivery') or None,
                notes=request.POST.get('notes', '').strip(),
                created_by=request.user,
                total_amount=0,
            )
            messages.success(request, f'Purchase order {po.po_number} created.')
            return redirect('purchase_order_detail', po_id=po.pk)
    return render(request, 'inventory/po_form.html', {'action': 'Create'})


# ── Purchase Order Detail ─────────────────────────────────────────────────────

@hms_permission_required('core.read_inventory')
def purchase_order_detail(request, po_id):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('created_by', 'approved_by').prefetch_related('items__inventory_item'),
        pk=po_id,
    )
    inventory_items = InventoryItem.objects.order_by('name')
    can_edit = po.status in [PurchaseOrder.Status.DRAFT]
    can_submit = po.status == PurchaseOrder.Status.DRAFT
    can_approve = po.status == PurchaseOrder.Status.SUBMITTED
    can_receive = po.status == PurchaseOrder.Status.APPROVED
    can_cancel = po.status not in [PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CANCELLED]

    return render(request, 'inventory/po_detail.html', {
        'po': po,
        'inventory_items': inventory_items,
        'can_edit': can_edit,
        'can_submit': can_submit,
        'can_approve': can_approve,
        'can_receive': can_receive,
        'can_cancel': can_cancel,
    })


# ── Purchase Order Add Item ───────────────────────────────────────────────────

@hms_permission_required('core.create_purchase_order')
@require_POST
def purchase_order_add_item(request, po_id):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    if po.status != PurchaseOrder.Status.DRAFT:
        messages.error(request, 'Cannot add items to a non-draft purchase order.')
        return redirect('purchase_order_detail', po_id=po_id)

    item_name = request.POST.get('item_name', '').strip()
    inventory_item_id = request.POST.get('inventory_item') or None
    try:
        quantity_ordered = int(request.POST.get('quantity_ordered', 1) or 1)
        unit_cost = float(request.POST.get('unit_cost', 0) or 0)
    except (ValueError, TypeError):
        messages.error(request, 'Invalid quantity or unit cost.')
        return redirect('purchase_order_detail', po_id=po_id)

    if not item_name and not inventory_item_id:
        messages.error(request, 'Provide an item name or select an inventory item.')
        return redirect('purchase_order_detail', po_id=po_id)

    inv_item = None
    if inventory_item_id:
        inv_item = get_object_or_404(InventoryItem, pk=inventory_item_id)
        if not item_name:
            item_name = inv_item.name

    PurchaseOrderItem.objects.create(
        purchase_order=po,
        inventory_item=inv_item,
        item_name=item_name,
        quantity_ordered=quantity_ordered,
        unit_cost=unit_cost,
        quantity_received=0,
    )

    # Recalculate total
    total = po.items.aggregate(s=Sum('total'))['s'] or 0
    po.total_amount = total
    po.save()

    messages.success(request, f'Item "{item_name}" added to PO.')
    return redirect('purchase_order_detail', po_id=po_id)


# ── Purchase Order Update Status ──────────────────────────────────────────────

@hms_permission_required('core.approve_purchase_order')
@require_POST
def purchase_order_update_status(request, po_id):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    new_status = request.POST.get('new_status', '').strip()

    valid_transitions = {
        PurchaseOrder.Status.DRAFT: [PurchaseOrder.Status.SUBMITTED, PurchaseOrder.Status.CANCELLED],
        PurchaseOrder.Status.SUBMITTED: [PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.CANCELLED],
        PurchaseOrder.Status.APPROVED: [PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CANCELLED],
    }
    allowed = valid_transitions.get(po.status, [])
    if new_status not in allowed:
        messages.error(request, f'Cannot transition from {po.status} to {new_status}.')
        return redirect('purchase_order_detail', po_id=po_id)

    with transaction.atomic():
        po.status = new_status
        if new_status == PurchaseOrder.Status.APPROVED:
            po.approved_by = request.user
        if new_status == PurchaseOrder.Status.RECEIVED:
            po.received_at = timezone.now()
            # Update inventory quantities
            for item in po.items.select_related('inventory_item'):
                if item.inventory_item:
                    inv_item = item.inventory_item
                    inv_item.quantity_in_stock += item.quantity_ordered
                    inv_item.save()
                    item.quantity_received = item.quantity_ordered
                    item.save()
                    InventoryTransaction.objects.create(
                        inventory_item=inv_item,
                        transaction_type=InventoryTransaction.TxType.PURCHASE,
                        quantity_in=item.quantity_ordered,
                        balance_after=inv_item.quantity_in_stock,
                        unit_cost=item.unit_cost,
                        reference_number=po.po_number,
                        notes=f'Goods received from {po.supplier_name} ({po.po_number}).',
                        performed_by=request.user,
                    )
        po.save()

    messages.success(request, f'Purchase order status updated to {new_status}.')
    return redirect('purchase_order_detail', po_id=po_id)


# ── Purchase Report ───────────────────────────────────────────────────────────

@hms_permission_required('core.view_store_reports')
def report_purchase_orders(request):
    """Purchase Report — purchase orders with date/status/supplier filters and totals."""
    pos = PurchaseOrder.objects.select_related('created_by', 'approved_by').order_by('-ordered_at')

    status_f = request.GET.get('status', '')
    supplier_q = request.GET.get('supplier', '').strip()
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')

    if status_f:
        pos = pos.filter(status=status_f)
    if supplier_q:
        pos = pos.filter(supplier_name__icontains=supplier_q)
    if date_from:
        pos = pos.filter(ordered_at__date__gte=date_from)
    if date_to:
        pos = pos.filter(ordered_at__date__lte=date_to)

    total_value = pos.aggregate(s=Sum('total_amount'))['s'] or 0

    headers = ['PO Number', 'Supplier', 'Status', 'Ordered At', 'Expected Delivery', 'Total Amount', 'Created By']
    rows = [
        [po.po_number, po.supplier_name, po.status,
         po.ordered_at.strftime('%Y-%m-%d'), str(po.expected_delivery or ''),
         po.total_amount, po.created_by.get_full_name() or po.created_by.username]
        for po in pos
    ]

    if request.GET.get('export') == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="purchase_report.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response

    if request.GET.get('export') == 'excel':
        from .report_export import export_excel
        return export_excel('purchase_report.xlsx', headers, rows, title='Purchase Report')

    paginator = Paginator(pos, 30)
    page_obj = paginator.get_page(request.GET.get('page'))
    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))

    return render(request, 'inventory/reports/purchase_report.html', {
        'page_obj': page_obj,
        'status_f': status_f,
        'supplier_q': supplier_q,
        'date_from': date_from,
        'date_to': date_to,
        'status_choices': PurchaseOrder.Status.choices,
        'total_value': total_value,
        'qp': qp,
    })


# ── URL Reference ─────────────────────────────────────────────────────────────
# inventory_dashboard          GET  /inventory/
# inventory_item_list          GET  /inventory/items/
# inventory_item_create        GET/POST  /inventory/items/create/
# inventory_item_edit          GET/POST  /inventory/items/<item_id>/edit/
# inventory_item_adjust        POST /inventory/items/<item_id>/adjust/
# purchase_order_list          GET  /inventory/purchase-orders/
# purchase_order_create        GET/POST  /inventory/purchase-orders/create/
# purchase_order_detail        GET  /inventory/purchase-orders/<po_id>/
# purchase_order_add_item      POST /inventory/purchase-orders/<po_id>/add-item/
# purchase_order_update_status POST /inventory/purchase-orders/<po_id>/update-status/
