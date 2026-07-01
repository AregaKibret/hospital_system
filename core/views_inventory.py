from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, InventoryCategory, InventoryItem, PurchaseOrder, PurchaseOrderItem


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

    items = InventoryItem.objects.select_related('category').order_by('name')

    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(supplier_name__icontains=query)
        )
    if category_id:
        items = items.filter(category_id=category_id)

    categories = InventoryCategory.objects.all()
    paginator = Paginator(items, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'inventory/item_list.html', {
        'page_obj': page_obj,
        'query': query,
        'category_id': category_id,
        'categories': categories,
    })


# ── Inventory Item Create ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_inventory')
def inventory_item_create(request):
    categories = InventoryCategory.objects.all()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Item name is required.')
        else:
            category_id = request.POST.get('category') or None
            inv_item = InventoryItem.objects.create(
                name=name,
                category_id=category_id,
                sku=request.POST.get('sku', '').strip(),
                unit=request.POST.get('unit', 'units').strip() or 'units',
                quantity_in_stock=int(request.POST.get('quantity_in_stock', 0) or 0),
                unit_cost=request.POST.get('unit_cost', 0) or 0,
                reorder_level=int(request.POST.get('reorder_level', 10) or 10),
                supplier_name=request.POST.get('supplier_name', '').strip(),
                notes=request.POST.get('notes', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.INVENTORY,
                object_type='InventoryItem', object_id=inv_item.pk, object_repr=inv_item.name,
                description=f'Inventory item "{inv_item.name}" created — qty {inv_item.quantity_in_stock}',
                request=request,
            )
            messages.success(request, 'Inventory item created successfully.')
            return redirect('inventory_item_list')
    return render(request, 'inventory/item_form.html', {
        'categories': categories,
        'action': 'Create',
        'item': None,
    })


# ── Inventory Item Edit ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_inventory')
def inventory_item_edit(request, item_id):
    item = get_object_or_404(InventoryItem, pk=item_id)
    categories = InventoryCategory.objects.all()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Item name is required.')
        else:
            item.name = name
            item.category_id = request.POST.get('category') or None
            item.sku = request.POST.get('sku', '').strip()
            item.unit = request.POST.get('unit', 'units').strip() or 'units'
            item.unit_cost = request.POST.get('unit_cost', 0) or 0
            item.reorder_level = int(request.POST.get('reorder_level', 10) or 10)
            item.supplier_name = request.POST.get('supplier_name', '').strip()
            item.notes = request.POST.get('notes', '').strip()
            item.save()
            messages.success(request, 'Item updated successfully.')
            return redirect('inventory_item_list')
    return render(request, 'inventory/item_form.html', {
        'item': item,
        'categories': categories,
        'action': 'Edit',
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
                    item.inventory_item.quantity_in_stock += item.quantity_ordered
                    item.inventory_item.save()
                    item.quantity_received = item.quantity_ordered
                    item.save()
        po.save()

    messages.success(request, f'Purchase order status updated to {new_status}.')
    return redirect('purchase_order_detail', po_id=po_id)


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
