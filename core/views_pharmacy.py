from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, Dispensing, MedicationOrder, Patient, PharmacyStock


# ── Pharmacy Dashboard ────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def pharmacy_dashboard(request):
    today = timezone.localdate()

    all_stock = PharmacyStock.objects.all()
    total_drugs = all_stock.count()

    # Low stock (quantity <= reorder_level)
    low_stock_items = [s for s in all_stock if s.is_low_stock and not s.is_expired]
    low_stock_count = len(low_stock_items)

    # Expired
    expired_items = [s for s in all_stock if s.is_expired]
    expired_count = len(expired_items)

    # Today's dispensings
    todays_dispensings = (
        Dispensing.objects
        .select_related('patient', 'dispensed_by', 'pharmacy_stock')
        .filter(dispensed_at__date=today)
        .order_by('-dispensed_at')
    )
    todays_count = todays_dispensings.count()

    # Recent low stock for alerts section (up to 8)
    low_stock_qs = PharmacyStock.objects.filter(
        models_reorder_level_expr()
    ).order_by('quantity_in_stock')[:8]

    return render(request, 'pharmacy/dashboard.html', {
        'today': today,
        'total_drugs': total_drugs,
        'low_stock_count': low_stock_count,
        'expired_count': expired_count,
        'todays_count': todays_count,
        'todays_dispensings': todays_dispensings[:10],
        'low_stock_alerts': low_stock_qs,
    })


def models_reorder_level_expr():
    """Helper returning a Q for stocks where qty <= reorder_level.
    Since quantity_in_stock and reorder_level are on the same model,
    we use a raw F expression comparison."""
    from django.db.models import F
    return Q(quantity_in_stock__lte=F('reorder_level'))


# ── Stock List ────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_medication_inventory')
def pharmacy_stock_list(request):
    from django.db.models import F

    search = request.GET.get('q', '').strip()
    stock_filter = request.GET.get('filter', 'all')

    qs = PharmacyStock.objects.all()

    if search:
        qs = qs.filter(
            Q(drug_name__icontains=search)
            | Q(generic_name__icontains=search)
            | Q(category__icontains=search)
            | Q(batch_number__icontains=search)
        )

    today = timezone.localdate()
    if stock_filter == 'low_stock':
        qs = qs.filter(quantity_in_stock__lte=F('reorder_level'))
    elif stock_filter == 'expired':
        qs = qs.filter(expiry_date__lte=today)
    elif stock_filter == 'out_of_stock':
        qs = qs.filter(quantity_in_stock=0)

    qs = qs.order_by('drug_name')
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'pharmacy/stock_list.html', {
        'page_obj': page_obj,
        'search': search,
        'stock_filter': stock_filter,
        'today': today,
    })


# ── Stock Create ──────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_medication')
def pharmacy_stock_create(request):
    if request.method == 'POST':
        data = request.POST
        try:
            stock = PharmacyStock.objects.create(
                drug_name=data['drug_name'].strip(),
                generic_name=data.get('generic_name', '').strip(),
                category=data.get('category', '').strip(),
                dosage_form=data.get('dosage_form', PharmacyStock.DosageForm.TABLET),
                strength=data.get('strength', '').strip(),
                quantity_in_stock=int(data.get('quantity_in_stock', 0)),
                unit=data.get('unit', 'Tablets').strip() or 'Tablets',
                unit_cost=data.get('unit_cost', 0),
                selling_price=data.get('selling_price', 0),
                reorder_level=int(data.get('reorder_level', 50)),
                batch_number=data.get('batch_number', '').strip(),
                expiry_date=data.get('expiry_date') or None,
                supplier=data.get('supplier', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.PHARMACY,
                object_type='PharmacyStock', object_id=stock.pk, object_repr=stock.drug_name,
                description=f'Drug "{stock.drug_name}" added to pharmacy stock — qty {stock.quantity_in_stock}',
                extra_data={'batch': stock.batch_number, 'quantity': stock.quantity_in_stock},
                request=request,
            )
            messages.success(request, 'Drug added to stock successfully.')
            return redirect('pharmacy_stock_list')
        except Exception as exc:
            messages.error(request, f'Error saving record: {exc}')

    return render(request, 'pharmacy/stock_form.html', {
        'form_title': 'Add Drug to Stock',
        'dosage_forms': PharmacyStock.DosageForm.choices,
        'action': 'create',
    })


# ── Stock Edit ────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_medication')
def pharmacy_stock_edit(request, stock_id):
    stock = get_object_or_404(PharmacyStock, pk=stock_id)

    if request.method == 'POST':
        data = request.POST
        try:
            stock.drug_name = data['drug_name'].strip()
            stock.generic_name = data.get('generic_name', '').strip()
            stock.category = data.get('category', '').strip()
            stock.dosage_form = data.get('dosage_form', stock.dosage_form)
            stock.strength = data.get('strength', '').strip()
            stock.quantity_in_stock = int(data.get('quantity_in_stock', stock.quantity_in_stock))
            stock.unit = data.get('unit', stock.unit).strip() or stock.unit
            stock.unit_cost = data.get('unit_cost', stock.unit_cost)
            stock.selling_price = data.get('selling_price', stock.selling_price)
            stock.reorder_level = int(data.get('reorder_level', stock.reorder_level))
            stock.batch_number = data.get('batch_number', '').strip()
            stock.expiry_date = data.get('expiry_date') or None
            stock.supplier = data.get('supplier', '').strip()
            stock.save()
            messages.success(request, f'"{stock.drug_name}" updated successfully.')
            return redirect('pharmacy_stock_list')
        except Exception as exc:
            messages.error(request, f'Error updating record: {exc}')

    return render(request, 'pharmacy/stock_form.html', {
        'form_title': f'Edit: {stock.drug_name}',
        'stock': stock,
        'dosage_forms': PharmacyStock.DosageForm.choices,
        'action': 'edit',
    })


# ── Stock Adjust ──────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_medication')
@require_POST
def pharmacy_stock_adjust(request, stock_id):
    stock = get_object_or_404(PharmacyStock, pk=stock_id)
    try:
        adjustment = int(request.POST.get('adjustment', 0))
        reason = request.POST.get('reason', '').strip()
        old_qty = stock.quantity_in_stock
        new_qty = stock.quantity_in_stock + adjustment
        if new_qty < 0:
            messages.error(request, 'Adjustment would result in negative stock. Operation cancelled.')
            return redirect('pharmacy_stock_list')
        stock.quantity_in_stock = new_qty
        stock.save()
        direction = 'Added' if adjustment >= 0 else 'Removed'
        log_action(
            request.user, AuditLog.Action.ADJUST, AuditLog.Module.PHARMACY,
            object_type='PharmacyStock', object_id=stock.pk, object_repr=stock.drug_name,
            description=f'Stock adjusted {adjustment:+d} for "{stock.drug_name}". Reason: {reason or "—"}',
            changes={'quantity_in_stock': {'old': str(old_qty), 'new': str(new_qty), 'diff': str(adjustment)}},
            extra_data={'reason': reason, 'batch': stock.batch_number},
            request=request,
        )
        messages.success(
            request,
            f'{direction} {abs(adjustment)} units for "{stock.drug_name}". '
            f'New stock: {new_qty}. Reason: {reason or "—"}'
        )
    except (ValueError, TypeError):
        messages.error(request, 'Invalid adjustment value.')
    return redirect('pharmacy_stock_list')


# ── Prescriptions (pending dispensing) ───────────────────────────────────────

@hms_permission_required('core.read_prescription_for_dispensing')
def pharmacy_prescriptions(request):
    search = request.GET.get('q', '').strip()

    # Active medication orders not yet dispensed
    qs = (
        MedicationOrder.objects
        .filter(status=MedicationOrder.Status.ACTIVE)
        .exclude(dispensing__status=Dispensing.Status.DISPENSED)
        .select_related('visit__patient', 'ordered_by')
        .order_by('-ordered_at')
    )

    if search:
        qs = qs.filter(
            Q(visit__patient__first_name__icontains=search)
            | Q(visit__patient__last_name__icontains=search)
            | Q(visit__patient__card_number__icontains=search)
            | Q(drug_name__icontains=search)
        )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'pharmacy/prescriptions.html', {
        'page_obj': page_obj,
        'search': search,
    })


# ── Dispense from Prescription ────────────────────────────────────────────────

@hms_permission_required('core.dispense_medication')
def pharmacy_dispense(request, order_id):
    order = get_object_or_404(
        MedicationOrder.objects.select_related('visit__patient', 'ordered_by'),
        pk=order_id,
    )
    # Already dispensed guard
    if hasattr(order, 'dispensing') and order.dispensing.status == Dispensing.Status.DISPENSED:
        messages.warning(request, 'This order has already been fully dispensed.')
        return redirect('pharmacy_prescriptions')

    stock_items = PharmacyStock.objects.filter(quantity_in_stock__gt=0).order_by('drug_name')

    if request.method == 'POST':
        data = request.POST
        try:
            qty = int(data.get('quantity_dispensed', 1))
            unit_price_val = data.get('unit_price', '0')
            stock_id = data.get('pharmacy_stock')
            status_val = data.get('status', Dispensing.Status.DISPENSED)
            notes_val = data.get('notes', '').strip()

            stock = None
            if stock_id:
                stock = get_object_or_404(PharmacyStock, pk=stock_id)
                if stock.quantity_in_stock < qty:
                    messages.error(
                        request,
                        f'Insufficient stock. Available: {stock.quantity_in_stock} {stock.unit}.'
                    )
                    return render(request, 'pharmacy/dispense_form.html', {
                        'order': order,
                        'stock_items': stock_items,
                    })

            from decimal import Decimal
            unit_price = Decimal(unit_price_val)
            total = unit_price * qty

            with transaction.atomic():
                # If updating existing dispensing record
                if hasattr(order, 'dispensing'):
                    d = order.dispensing
                    d.pharmacy_stock = stock
                    d.quantity_dispensed = qty
                    d.unit_price = unit_price
                    d.total_amount = total
                    d.dispensed_by = request.user
                    d.status = status_val
                    d.notes = notes_val
                    d.save()
                else:
                    Dispensing.objects.create(
                        medication_order=order,
                        pharmacy_stock=stock,
                        patient=order.visit.patient,
                        drug_name=order.drug_name,
                        quantity_dispensed=qty,
                        unit_price=unit_price,
                        total_amount=total,
                        dispensed_by=request.user,
                        status=status_val,
                        notes=notes_val,
                    )

                # Deduct from stock
                if stock:
                    stock.quantity_in_stock -= qty
                    stock.save()

                # Mark order completed if fully dispensed
                if status_val == Dispensing.Status.DISPENSED:
                    order.status = MedicationOrder.Status.COMPLETED
                    order.save()

            log_action(
                request.user, AuditLog.Action.DISPENSE, AuditLog.Module.PHARMACY,
                object_type='Dispensing', object_id=order.pk,
                object_repr=f'{order.drug_name} / {order.visit.patient.full_name}',
                description=f'Dispensed {qty} units of {order.drug_name} for {order.visit.patient.full_name}',
                extra_data={
                    'drug': order.drug_name,
                    'quantity': qty,
                    'patient': order.visit.patient.full_name,
                    'unit_price': str(unit_price),
                    'total': str(total),
                },
                request=request,
            )
            messages.success(request, f'Dispensed {qty} units of {order.drug_name} for {order.visit.patient.full_name}.')
            return redirect('pharmacy_prescriptions')

        except Exception as exc:
            messages.error(request, f'Error processing dispensing: {exc}')

    return render(request, 'pharmacy/dispense_form.html', {
        'order': order,
        'stock_items': stock_items,
        'dispense_statuses': Dispensing.Status.choices,
    })


# ── OTC Sales ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_sales(request):
    today = timezone.localdate()

    if request.method == 'POST':
        data = request.POST
        try:
            patient_id = data.get('patient_id')
            stock_id = data.get('pharmacy_stock')
            qty = int(data.get('quantity', 1))
            notes_val = data.get('notes', '').strip()

            patient = get_object_or_404(Patient, pk=patient_id)
            stock = get_object_or_404(PharmacyStock, pk=stock_id)

            if stock.quantity_in_stock < qty:
                messages.error(
                    request,
                    f'Insufficient stock for "{stock.drug_name}". Available: {stock.quantity_in_stock}.'
                )
            else:
                from decimal import Decimal
                unit_price = stock.selling_price
                total = unit_price * qty

                with transaction.atomic():
                    Dispensing.objects.create(
                        medication_order=None,
                        pharmacy_stock=stock,
                        patient=patient,
                        drug_name=stock.drug_name,
                        quantity_dispensed=qty,
                        unit_price=unit_price,
                        total_amount=total,
                        dispensed_by=request.user,
                        status=Dispensing.Status.DISPENSED,
                        notes=notes_val,
                    )
                    stock.quantity_in_stock -= qty
                    stock.save()

                messages.success(
                    request,
                    f'Sale recorded: {qty} x {stock.drug_name} for {patient.full_name}.'
                )
                return redirect('pharmacy_sales')

        except Exception as exc:
            messages.error(request, f'Error processing sale: {exc}')

    # Patient search for form
    patient_search = request.GET.get('patient_q', '').strip()
    patients = []
    if patient_search:
        patients = Patient.objects.filter(
            Q(first_name__icontains=patient_search)
            | Q(last_name__icontains=patient_search)
            | Q(card_number__icontains=patient_search)
        )[:10]

    stock_items = PharmacyStock.objects.filter(quantity_in_stock__gt=0).order_by('drug_name')
    todays_sales = (
        Dispensing.objects
        .select_related('patient', 'pharmacy_stock', 'dispensed_by')
        .filter(dispensed_at__date=today, medication_order__isnull=True)
        .order_by('-dispensed_at')
    )

    return render(request, 'pharmacy/sales.html', {
        'today': today,
        'stock_items': stock_items,
        'todays_sales': todays_sales,
        'patients': patients,
        'patient_search': patient_search,
    })


# ── URL_PATTERNS_TO_ADD (pharmacy) ────────────────────────────────────────────
# path('pharmacy/', views_pharmacy.pharmacy_dashboard, name='pharmacy_dashboard'),
# path('pharmacy/stock/', views_pharmacy.pharmacy_stock_list, name='pharmacy_stock_list'),
# path('pharmacy/stock/add/', views_pharmacy.pharmacy_stock_create, name='pharmacy_stock_create'),
# path('pharmacy/stock/<int:stock_id>/edit/', views_pharmacy.pharmacy_stock_edit, name='pharmacy_stock_edit'),
# path('pharmacy/stock/<int:stock_id>/adjust/', views_pharmacy.pharmacy_stock_adjust, name='pharmacy_stock_adjust'),
# path('pharmacy/prescriptions/', views_pharmacy.pharmacy_prescriptions, name='pharmacy_prescriptions'),
# path('pharmacy/dispense/<int:order_id>/', views_pharmacy.pharmacy_dispense, name='pharmacy_dispense'),
# path('pharmacy/sales/', views_pharmacy.pharmacy_sales, name='pharmacy_sales'),
