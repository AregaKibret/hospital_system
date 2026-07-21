import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .inventory_bridge import record_pharmacy_stock_transaction
from .models import (
    AuditLog, Invoice, InvoiceItem, MedicationBatch, Patient,
    PharmacyReturn, PharmacyReturnItem, PharmacySale, PharmacySaleItem,
    PharmacyStock, Medication, StockTransaction,
)


def _d(val, default='0'):
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _log_stock_movement(batch_obj, ps_obj, tx_type, qty, user, reference, notes, patient=None):
    """Write a StockTransaction ledger entry for a POS movement, regardless of
    whether the item came from the Medication catalog (batch_obj) or the
    legacy PharmacyStock (ps_obj)."""
    is_in = tx_type in (StockTransaction.TxType.RETURN, StockTransaction.TxType.ADJUSTMENT_IN)
    if batch_obj is not None:
        StockTransaction.objects.create(
            medication=batch_obj.medication,
            batch=batch_obj,
            transaction_type=tx_type,
            quantity_in=qty if is_in else 0,
            quantity_out=0 if is_in else qty,
            balance_after=batch_obj.medication.current_stock,
            unit_cost=batch_obj.purchase_price,
            total_value=batch_obj.purchase_price * qty,
            reference_number=reference,
            notes=notes,
            patient=patient,
            performed_by=user,
            transaction_date=timezone.now(),
        )
    elif ps_obj is not None:
        record_pharmacy_stock_transaction(
            ps_obj, tx_type, user,
            qty_in=qty if is_in else 0,
            qty_out=0 if is_in else qty,
            reference=reference,
            notes=notes,
            patient=patient,
        )


# ── AJAX: product search ───────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_product_search(request):
    q = request.GET.get('q', '').strip()
    results = []
    if len(q) >= 2:
        batches = (
            MedicationBatch.objects
            .select_related('medication')
            .filter(
                Q(medication__brand_name__icontains=q)
                | Q(medication__generic_name__icontains=q)
                | Q(batch_number__icontains=q),
                quantity_available__gt=0,
                expiration_date__gte=timezone.localdate(),
            )
            .order_by('medication__brand_name', 'expiration_date')[:20]
        )
        for b in batches:
            results.append({
                'id': f'batch:{b.pk}',
                'name': b.medication.name,
                'generic': b.medication.generic_name or '',
                'strength': b.medication.strength or '',
                'form': b.medication.dosage_form or '',
                'batch': b.batch_number,
                'expiry': b.expiration_date.strftime('%d %b %Y'),
                'stock': b.quantity_available,
                'price': float(b.medication.selling_price or 0),
                'source': 'batch',
            })

        stocks = (
            PharmacyStock.objects
            .filter(
                Q(drug_name__icontains=q),
                quantity_in_stock__gt=0,
            )
            .order_by('drug_name')[:10]
        )
        for s in stocks:
            results.append({
                'id': f'ps:{s.pk}',
                'name': s.drug_name,
                'generic': '',
                'strength': s.strength or '',
                'form': s.dosage_form or '',
                'batch': s.batch_number or '',
                'expiry': s.expiry_date.strftime('%d %b %Y') if s.expiry_date else '',
                'stock': s.quantity_in_stock,
                'price': float(s.selling_price or 0),
                'source': 'ps',
            })
    return JsonResponse({'results': results})


# ── AJAX: patient search ───────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_patient_search(request):
    q = request.GET.get('q', '').strip()
    results = []
    if len(q) >= 2:
        pts = Patient.objects.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(card_number__icontains=q)
            | Q(mobile__icontains=q)
        ).order_by('first_name')[:10]
        for p in pts:
            results.append({
                'id': p.pk,
                'name': p.full_name,
                'mrn': p.card_number,
                'phone': p.mobile or '',
            })
    return JsonResponse({'results': results})


# ── POS main screen ────────────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_pos(request):
    today = timezone.localdate()
    recent_sales = (
        PharmacySale.objects
        .select_related('cashier')
        .filter(created_at__date=today)
        .order_by('-created_at')[:10]
    )
    stats = PharmacySale.objects.filter(created_at__date=today).aggregate(
        total_sales=Count('pk'),
        total_revenue=Sum('total_amount'),
        paid_count=Count('pk', filter=Q(status='Paid')),
    )
    return render(request, 'pharmacy/pos.html', {
        'today': today,
        'recent_sales': recent_sales,
        'stats': stats,
    })


# ── Create sale (POST from POS) ────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
@require_POST
def pharmacy_sale_create(request):
    """
    Two paths:
    - Registered patient selected → charges sent to central Billing, dispense after payment.
    - Walk-in (no patient) → payment collected directly at pharmacy, stock deducted immediately.
    """
    try:
        cart_json = request.POST.get('cart', '[]')
        items_data = json.loads(cart_json)
        if not items_data:
            messages.error(request, 'Cart is empty.')
            return redirect('pharmacy_pos')

        patient_id = request.POST.get('patient_id') or None
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip()
        discount_amount = _d(request.POST.get('discount_amount', '0'))
        notes = request.POST.get('notes', '').strip()
        # ALL sales (walk-in AND registered patient) go through central billing
        via_billing = True

        with transaction.atomic():
            patient = Patient.objects.get(pk=patient_id) if patient_id else None

            # For walk-in without a patient record, use a sentinel patient so Invoice FK is satisfied
            invoice_patient = patient
            if not invoice_patient:
                invoice_patient, _ = Patient.objects.get_or_create(
                    card_number='WALKIN-OTC',
                    defaults={
                        'first_name': 'Walk-in',
                        'last_name': 'Customer',
                        'sex': 'Other',
                        'mobile': '0000000000',
                    }
                )

            sale = PharmacySale(
                sale_type=PharmacySale.SaleType.WALK_IN,
                patient=patient,
                customer_name=customer_name if not patient else '',
                customer_phone=customer_phone,
                payment_method='Cash',
                discount_amount=discount_amount,
                cashier=request.user,
                notes=notes,
                status=PharmacySale.Status.PENDING,
            )
            sale.save()

            # Always create a billing invoice — cashier collects payment, pharmacy dispenses after
            invoice = Invoice.objects.create(
                patient=invoice_patient,
                visit=None,
                created_by=request.user,
                status='Draft',
                payment_type='Cash',
                total_amount=Decimal('0'),
                notes=(
                    f'Pharmacy OTC Sale — {sale.sale_number}'
                    + (f' | Customer: {customer_name}' if customer_name else '')
                    + (f' | Phone: {customer_phone}' if customer_phone else '')
                ),
            )
            sale.invoice = invoice

            subtotal = Decimal('0')
            for row in items_data:
                src = row.get('src', '')
                qty = int(row.get('qty', 1))
                unit_price = _d(row.get('price', '0'))
                item_discount = _d(row.get('discount', '0'))
                drug_name = row.get('name', '')
                batch_number = ''
                expiry_date = None
                med_obj = None
                batch_obj = None
                ps_obj = None

                if src.startswith('batch:'):
                    batch_obj = MedicationBatch.objects.select_for_update().get(pk=int(src.split(':')[1]))
                    if batch_obj.quantity_available < qty:
                        raise ValueError(
                            f'Insufficient stock for {batch_obj.medication.name} '
                            f'(available: {batch_obj.quantity_available})'
                        )
                    med_obj = batch_obj.medication
                    batch_number = batch_obj.batch_number
                    expiry_date = batch_obj.expiration_date
                    if not via_billing:
                        batch_obj.quantity_available -= qty
                        batch_obj.save(update_fields=['quantity_available'])
                        _log_stock_movement(
                            batch_obj, None, StockTransaction.TxType.DISPENSE, qty, request.user,
                            reference=f'SALE-{sale.sale_number}',
                            notes=f'Walk-in sale {sale.sale_number}.',
                            patient=patient,
                        )

                elif src.startswith('ps:'):
                    ps_obj = PharmacyStock.objects.select_for_update().get(pk=int(src.split(':')[1]))
                    if ps_obj.quantity_in_stock < qty:
                        raise ValueError(
                            f'Insufficient stock for {ps_obj.drug_name} '
                            f'(available: {ps_obj.quantity_in_stock})'
                        )
                    batch_number = ps_obj.batch_number or ''
                    expiry_date = ps_obj.expiry_date
                    if not via_billing:
                        ps_obj.quantity_in_stock -= qty
                        ps_obj.save(update_fields=['quantity_in_stock'])
                        _log_stock_movement(
                            None, ps_obj, StockTransaction.TxType.DISPENSE, qty, request.user,
                            reference=f'SALE-{sale.sale_number}',
                            notes=f'Walk-in sale {sale.sale_number}.',
                            patient=patient,
                        )

                item = PharmacySaleItem(
                    sale=sale,
                    medication=med_obj,
                    medication_batch=batch_obj,
                    pharmacy_stock=ps_obj,
                    drug_name=drug_name,
                    quantity=qty,
                    unit_price=unit_price,
                    discount=item_discount,
                    batch_number=batch_number,
                    expiry_date=expiry_date,
                    dispensed=not via_billing,  # walk-in: dispense immediately after payment
                )
                item.save()
                subtotal += item.total

                if via_billing:
                    item_desc = drug_name
                    if batch_number:
                        item_desc += f' (Batch: {batch_number})'
                    item_desc += f' — Sale {sale.sale_number}'
                    InvoiceItem.objects.create(
                        invoice=invoice,
                        description=item_desc[:255],
                        service_type='Medication',
                        quantity=qty,
                        unit_price=unit_price,
                        total=item.total,
                    )

            sale.subtotal = subtotal
            sale.total_amount = max(subtotal - discount_amount, Decimal('0'))

            # Payment is always collected at the central billing/cashier counter
            sale.status = PharmacySale.Status.PENDING
            invoice.total_amount = (
                invoice.items.aggregate(t=Sum('total'))['t'] or Decimal('0')
            )
            invoice.save(update_fields=['total_amount', 'updated_at'])

            sale.save()

            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.PHARMACY,
                object_type='PharmacySale', object_id=sale.pk,
                object_repr=sale.sale_number,
                description=(
                    f'Pharmacy sale {sale.sale_number} — '
                    f'{patient.full_name if patient else customer_name or "Walk-in"} — '
                    f'ETB {sale.total_amount} — '
                    f'{"sent to billing" if via_billing else "paid directly"}'
                ),
                request=request,
            )

        messages.success(
            request,
            f'Sale {sale.sale_number} sent to billing. '
            f'ETB {sale.total_amount:,.2f} — Invoice #{invoice.invoice_number}. '
            f'Dispense after cashier confirms payment.'
        )
        return redirect('pharmacy_sale_detail', sale_id=sale.pk)

    except Patient.DoesNotExist:
        messages.error(request, 'Patient not found.')
    except (MedicationBatch.DoesNotExist, PharmacyStock.DoesNotExist) as e:
        messages.error(request, f'Stock record not found: {e}')
    except (ValueError, json.JSONDecodeError) as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f'Error creating sale: {e}')
    return redirect('pharmacy_pos')


# ── Confirm dispense after billing payment ─────────────────────────────────────

@hms_permission_required('core.dispense_medication')
@require_POST
def pharmacy_sale_dispense(request, sale_id):
    """Release medications and deduct inventory once billing confirms payment."""
    sale = get_object_or_404(PharmacySale, pk=sale_id)

    if sale.status == PharmacySale.Status.DISPENSED:
        messages.info(request, 'Sale already dispensed.')
        return redirect('pharmacy_sale_detail', sale_id=sale_id)

    if sale.status == PharmacySale.Status.CANCELLED:
        messages.error(request, 'Cannot dispense a cancelled sale.')
        return redirect('pharmacy_sale_detail', sale_id=sale_id)

    # Verify billing payment
    invoice_paid = False
    if sale.invoice_id:
        invoice_paid = Invoice.objects.filter(
            pk=sale.invoice_id,
            status__in=['Paid', 'Overpaid', 'Waived', 'Partial', 'Credit Pending'],
        ).exists()

    if not invoice_paid:
        messages.error(
            request,
            'Cannot dispense: the billing invoice has not been paid. '
            'Please ask the patient to pay at the cashier counter first.'
        )
        return redirect('pharmacy_sale_detail', sale_id=sale_id)

    pending_items = sale.items.filter(dispensed=False).select_related(
        'medication_batch', 'pharmacy_stock'
    )
    if not pending_items.exists():
        messages.info(request, 'All items already dispensed.')
        return redirect('pharmacy_sale_detail', sale_id=sale_id)

    try:
        with transaction.atomic():
            for item in pending_items.select_for_update():
                qty = item.quantity
                if item.medication_batch_id:
                    b = item.medication_batch
                    if b.quantity_available < qty:
                        raise ValueError(
                            f'Insufficient stock for {item.drug_name}: '
                            f'need {qty}, available {b.quantity_available}'
                        )
                    b.quantity_available -= qty
                    b.save(update_fields=['quantity_available'])
                    _log_stock_movement(
                        b, None, StockTransaction.TxType.DISPENSE, qty, request.user,
                        reference=f'SALE-{sale.sale_number}',
                        notes=f'Dispensed for sale {sale.sale_number} ({sale.customer_display}).',
                        patient=sale.patient,
                    )

                elif item.pharmacy_stock_id:
                    s = item.pharmacy_stock
                    if s.quantity_in_stock < qty:
                        raise ValueError(
                            f'Insufficient stock for {item.drug_name}: '
                            f'need {qty}, available {s.quantity_in_stock}'
                        )
                    s.quantity_in_stock -= qty
                    s.save(update_fields=['quantity_in_stock'])
                    _log_stock_movement(
                        None, s, StockTransaction.TxType.DISPENSE, qty, request.user,
                        reference=f'SALE-{sale.sale_number}',
                        notes=f'Dispensed for sale {sale.sale_number} ({sale.customer_display}).',
                        patient=sale.patient,
                    )

                item.dispensed = True
                item.dispensed_at = timezone.now()
                item.dispensed_by = request.user
                item.save(update_fields=['dispensed', 'dispensed_at', 'dispensed_by'])

            sale.status = PharmacySale.Status.DISPENSED
            sale.dispensed_by = request.user
            sale.dispensed_at = timezone.now()
            sale.save(update_fields=['status', 'dispensed_by', 'dispensed_at', 'updated_at'])

        log_action(
            request.user, AuditLog.Action.DISPENSE, AuditLog.Module.PHARMACY,
            object_type='PharmacySale', object_id=sale.pk,
            object_repr=sale.sale_number,
            description=(
                f'Medications dispensed for sale {sale.sale_number} '
                f'— {sale.customer_display} (payment confirmed, inventory deducted)'
            ),
            request=request,
        )
        messages.success(
            request,
            f'Medications dispensed for {sale.sale_number}. Inventory updated.'
        )
    except Exception as exc:
        messages.error(request, f'Error dispensing: {exc}')

    return redirect('pharmacy_sale_detail', sale_id=sale_id)


# ── Sale list ──────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_sales_list(request):
    status_filter = request.GET.get('status', 'all')
    search = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    qs = PharmacySale.objects.select_related('patient', 'cashier').order_by('-created_at')
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            Q(sale_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__card_number__icontains=search)
        )
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    today_stats = PharmacySale.objects.filter(
        created_at__date=timezone.localdate(),
        status__in=[PharmacySale.Status.DISPENSED],
    ).aggregate(total=Sum('paid_amount'), count=Count('pk'))

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'pharmacy/sales_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'search': search,
        'date_from': date_from,
        'date_to': date_to,
        'status_choices': PharmacySale.Status.choices,
        'today_stats': today_stats,
    })


# ── Sale detail ────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_sale_detail(request, sale_id):
    sale = get_object_or_404(
        PharmacySale.objects.select_related('patient', 'cashier', 'dispensed_by'),
        pk=sale_id,
    )
    items = sale.items.select_related('medication', 'medication_batch', 'pharmacy_stock', 'dispensed_by').all()
    returns = sale.returns.select_related('requested_by', 'approved_by').all()
    return render(request, 'pharmacy/sale_detail.html', {
        'sale': sale,
        'items': items,
        'returns': returns,
    })


# ── Sale receipt (printable) ───────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
def pharmacy_sale_receipt(request, sale_id):
    sale = get_object_or_404(PharmacySale.objects.select_related('patient', 'cashier'), pk=sale_id)
    items = sale.items.all()
    return render(request, 'pharmacy/sale_receipt.html', {
        'sale': sale,
        'items': items,
    })




# ── Cancel sale ────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_pharmacy_sale')
@require_POST
def pharmacy_sale_cancel(request, sale_id):
    sale = get_object_or_404(PharmacySale, pk=sale_id)
    if sale.status == PharmacySale.Status.CANCELLED:
        messages.error(request, 'Already cancelled.')
        return redirect('pharmacy_sale_detail', sale_id=sale.pk)

    reason = request.POST.get('reason', '').strip()
    with transaction.atomic():
        for item in sale.items.select_related('medication_batch', 'pharmacy_stock').all():
            if item.medication_batch_id:
                item.medication_batch.quantity_available += item.quantity
                item.medication_batch.save(update_fields=['quantity_available'])
                _log_stock_movement(
                    item.medication_batch, None, StockTransaction.TxType.RETURN, item.quantity, request.user,
                    reference=f'CANCEL-{sale.sale_number}',
                    notes=f'Sale {sale.sale_number} cancelled — stock restored. Reason: {reason}',
                    patient=sale.patient,
                )
            elif item.pharmacy_stock_id:
                item.pharmacy_stock.quantity_in_stock += item.quantity
                item.pharmacy_stock.save(update_fields=['quantity_in_stock'])
                _log_stock_movement(
                    None, item.pharmacy_stock, StockTransaction.TxType.RETURN, item.quantity, request.user,
                    reference=f'CANCEL-{sale.sale_number}',
                    notes=f'Sale {sale.sale_number} cancelled — stock restored. Reason: {reason}',
                    patient=sale.patient,
                )
        sale.status = PharmacySale.Status.CANCELLED
        sale.notes = (sale.notes + f'\nCancelled: {reason}').strip()
        sale.save()

        log_action(
            request.user, AuditLog.Action.CANCEL, AuditLog.Module.PHARMACY,
            object_type='PharmacySale', object_id=sale.pk,
            object_repr=sale.sale_number,
            description=f'Sale {sale.sale_number} cancelled. Reason: {reason}',
            request=request,
        )
    messages.success(request, f'Sale {sale.sale_number} cancelled and stock restored.')
    return redirect('pharmacy_sale_detail', sale_id=sale.pk)


# ── Return / Refund ────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_pharmacy_returns')
def pharmacy_return_create(request, sale_id):
    sale = get_object_or_404(PharmacySale, pk=sale_id)
    items = sale.items.all()

    if request.method == 'POST':
        return_type = request.POST.get('return_type')
        reason = request.POST.get('reason', '').strip()
        refund_method = request.POST.get('refund_method', '')
        restock = request.POST.get('restock', '1') == '1'

        return_items_data = []
        total_return = Decimal('0')
        for item in items:
            qty_key = f'qty_{item.pk}'
            qty = int(request.POST.get(qty_key, 0) or 0)
            if qty > 0 and qty <= item.quantity:
                total = _d(item.unit_price) * qty
                return_items_data.append({'item': item, 'qty': qty, 'total': total})
                total_return += total

        if not return_items_data:
            messages.error(request, 'No items selected for return.')
            return redirect('pharmacy_return_create', sale_id=sale.pk)

        with transaction.atomic():
            ret = PharmacyReturn(
                original_sale=sale,
                patient=sale.patient,
                customer_name=sale.customer_name,
                return_type=return_type,
                reason=reason,
                refund_method=refund_method,
                restock=restock,
                return_amount=total_return,
                requested_by=request.user,
                status=PharmacyReturn.Status.PENDING,
            )
            ret.save()
            for rd in return_items_data:
                PharmacyReturnItem.objects.create(
                    pharmacy_return=ret,
                    original_item=rd['item'],
                    drug_name=rd['item'].drug_name,
                    quantity=rd['qty'],
                    unit_price=rd['item'].unit_price,
                    total=rd['total'],
                )
            log_action(
                request.user, AuditLog.Action.RETURN, AuditLog.Module.PHARMACY,
                object_type='PharmacyReturn', object_id=ret.pk,
                object_repr=ret.return_number,
                description=f'Return {ret.return_number} for sale {sale.sale_number} — ETB {total_return}',
                request=request,
            )

        messages.success(request, f'Return {ret.return_number} submitted for approval.')
        return redirect('pharmacy_return_detail', return_id=ret.pk)

    return render(request, 'pharmacy/return_form.html', {
        'sale': sale,
        'items': items,
        'return_type_choices': PharmacyReturn.ReturnType.choices,
        'payment_choices': PharmacySale.PaymentMethod.choices,
    })


@hms_permission_required('core.manage_pharmacy_returns')
def pharmacy_return_detail(request, return_id):
    ret = get_object_or_404(
        PharmacyReturn.objects.select_related('original_sale', 'patient', 'requested_by', 'approved_by'),
        pk=return_id,
    )
    return render(request, 'pharmacy/return_detail.html', {
        'ret': ret,
        'items': ret.items.all(),
    })


@hms_permission_required('core.manage_pharmacy_returns')
@require_POST
def pharmacy_return_approve(request, return_id):
    ret = get_object_or_404(PharmacyReturn, pk=return_id)
    action = request.POST.get('action')

    with transaction.atomic():
        if action == 'approve':
            ret.status = PharmacyReturn.Status.APPROVED
            ret.approved_by = request.user
            if ret.restock:
                for ri in ret.items.select_related('original_item__medication_batch', 'original_item__pharmacy_stock').all():
                    if ri.original_item and ri.original_item.medication_batch_id:
                        b = ri.original_item.medication_batch
                        b.quantity_available += ri.quantity
                        b.save(update_fields=['quantity_available'])
                        _log_stock_movement(
                            b, None, StockTransaction.TxType.RETURN, ri.quantity, request.user,
                            reference=f'RTN-{ret.return_number}',
                            notes=f'Return {ret.return_number} restocked. Reason: {ret.reason}',
                            patient=ret.patient,
                        )
                    elif ri.original_item and ri.original_item.pharmacy_stock_id:
                        s = ri.original_item.pharmacy_stock
                        s.quantity_in_stock += ri.quantity
                        s.save(update_fields=['quantity_in_stock'])
                        _log_stock_movement(
                            None, s, StockTransaction.TxType.RETURN, ri.quantity, request.user,
                            reference=f'RTN-{ret.return_number}',
                            notes=f'Return {ret.return_number} restocked. Reason: {ret.reason}',
                            patient=ret.patient,
                        )
                    ri.restocked = True
                    ri.save(update_fields=['restocked'])
            log_action(
                request.user, AuditLog.Action.APPROVE, AuditLog.Module.PHARMACY,
                object_type='PharmacyReturn', object_id=ret.pk,
                object_repr=ret.return_number,
                description=f'Return {ret.return_number} approved',
                request=request,
            )
            messages.success(request, f'Return {ret.return_number} approved.')
        else:
            ret.status = PharmacyReturn.Status.REJECTED
            ret.approved_by = request.user
            messages.info(request, f'Return {ret.return_number} rejected.')
        ret.save()

    return redirect('pharmacy_return_detail', return_id=ret.pk)


# ── Reports ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_pharmacy_reports')
def pharmacy_report_daily(request):
    report_date = request.GET.get('date', str(timezone.localdate()))
    try:
        from datetime import date as _date
        rdate = _date.fromisoformat(report_date)
    except ValueError:
        rdate = timezone.localdate()

    sales = (
        PharmacySale.objects
        .filter(created_at__date=rdate, status__in=[
            PharmacySale.Status.DISPENSED, PharmacySale.Status.CREDIT
        ])
        .select_related('cashier', 'patient')
        .order_by('created_at')
    )
    by_method = {}
    for s in sales:
        pm = s.payment_method or 'Unknown'
        if pm not in by_method:
            by_method[pm] = {'count': 0, 'total': Decimal('0')}
        by_method[pm]['count'] += 1
        by_method[pm]['total'] += s.paid_amount

    totals = {
        'sales': sales.count(),
        'revenue': sum(s.paid_amount for s in sales),
        'outstanding': sum(s.total_amount - s.paid_amount for s in sales),
    }
    return render(request, 'pharmacy/report_daily.html', {
        'sales': sales,
        'report_date': rdate,
        'by_method': by_method,
        'totals': totals,
    })


@hms_permission_required('core.read_pharmacy_reports')
def pharmacy_report_medications(request):
    date_from = request.GET.get('date_from', str(timezone.localdate()))
    date_to = request.GET.get('date_to', str(timezone.localdate()))

    from django.db.models import F
    items = (
        PharmacySaleItem.objects
        .filter(
            sale__created_at__date__gte=date_from,
            sale__created_at__date__lte=date_to,
            sale__status__in=[PharmacySale.Status.DISPENSED, PharmacySale.Status.CREDIT],
        )
        .values('drug_name')
        .annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total'),
            transactions=Count('pk'),
        )
        .order_by('-revenue')
    )
    totals = {
        'revenue': sum(i['revenue'] or 0 for i in items),
        'qty': sum(i['qty_sold'] or 0 for i in items),
    }
    return render(request, 'pharmacy/report_medications.html', {
        'items': items,
        'date_from': date_from,
        'date_to': date_to,
        'totals': totals,
    })


@hms_permission_required('core.read_pharmacy_reports')
def pharmacy_report_credit(request):
    credits = (
        PharmacySale.objects
        .filter(status__in=[PharmacySale.Status.CREDIT])
        .select_related('patient', 'cashier')
        .order_by('credit_due_date', '-created_at')
    )
    totals = {
        'outstanding': sum(s.total_amount - s.paid_amount for s in credits),
        'count': credits.count(),
    }
    return render(request, 'pharmacy/report_credit.html', {
        'credits': credits,
        'totals': totals,
    })
