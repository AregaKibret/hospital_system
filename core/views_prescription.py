"""
Electronic Prescription Workflow
Doctor → Pharmacy → Inventory → Billing → Patient
"""
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Dispensing, Invoice, InvoiceItem, MAREntry, Medication,
    MedicationBatch, Patient, PharmacyStock, Prescription, PrescriptionItem,
    RxDispenseRecord, Visit,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rx_number():
    last = Prescription.objects.order_by('-id').first()
    nxt = (last.id + 1) if last else 1
    return f"RX-{nxt:06d}"


def _get_visit(visit_id):
    return get_object_or_404(
        Visit.objects.select_related('patient', 'department', 'doctor'),
        pk=visit_id,
    )


def _get_rx(rx_id):
    return get_object_or_404(
        Prescription.objects.select_related(
            'patient', 'visit__department', 'prescribed_by',
            'verified_by',
        ).prefetch_related('items__medication', 'items__dispense_records__dispensed_by'),
        pk=rx_id,
    )


# ── Medication AJAX search ────────────────────────────────────────────────────

@hms_permission_required('core.write_prescription')
def medication_search_api(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    meds = (
        Medication.objects
        .filter(is_active=True)
        .filter(
            Q(name__icontains=q)
            | Q(generic_name__icontains=q)
            | Q(code__icontains=q)
        )
        .select_related('category')
        .order_by('name')[:20]
    )

    # Pre-compute stock per medication
    batch_totals: dict[int, int] = {}
    med_ids = [m.pk for m in meds]
    for row in (
        MedicationBatch.objects
        .filter(medication_id__in=med_ids, is_active=True, quantity_available__gt=0)
        .values('medication_id')
        .annotate(total=Sum('quantity_available'))
    ):
        batch_totals[row['medication_id']] = row['total']

    results = []
    for m in meds:
        stock_qty = batch_totals.get(m.pk, 0)
        results.append({
            'id': m.pk,
            'name': m.name,
            'generic_name': m.generic_name,
            'strength': m.strength,
            'dosage_form': m.drug_type,
            'route': m.route,
            'dispensing_unit': m.dispensing_unit or m.unit_of_measure,
            'selling_price': str(m.selling_price),
            'stock_qty': stock_qty,
            'stock_status': 'available' if stock_qty > 0 else 'out_of_stock',
            'controlled': m.controlled_substance,
        })
    return JsonResponse({'results': results})


# ═══════════════════════════════════════════════════════════════════════
# DOCTOR SIDE
# ═══════════════════════════════════════════════════════════════════════

@hms_permission_required('core.write_prescription')
def prescription_create(request, visit_id):
    visit = _get_visit(visit_id)
    patient = visit.patient

    # Pre-fill diagnosis from last active diagnosis
    from .models import Diagnosis
    last_dx = (
        Diagnosis.objects.filter(visit=visit, status__in=['Active', 'Chronic'])
        .order_by('-created_at').first()
    )

    if request.method == 'POST':
        diagnosis = request.POST.get('diagnosis', '').strip()
        notes = request.POST.get('notes', '').strip()

        drug_names   = request.POST.getlist('drug_name')
        doses        = request.POST.getlist('dose')
        routes       = request.POST.getlist('route')
        frequencies  = request.POST.getlist('frequency')
        durations    = request.POST.getlist('duration_days')
        quantities   = request.POST.getlist('quantity')
        meal_instrs  = request.POST.getlist('meal_instruction')
        specials     = request.POST.getlist('special_instructions')
        med_ids_list = request.POST.getlist('medication_id')

        # Validate at least one medication
        valid = [(i, n) for i, n in enumerate(drug_names) if n.strip()]
        if not valid:
            messages.error(request, 'Add at least one medication.')
            return _render_create(request, visit, last_dx, diagnosis, notes)

        try:
            with transaction.atomic():
                rx = Prescription.objects.create(
                    visit=visit,
                    patient=patient,
                    prescribed_by=request.user,
                    diagnosis=diagnosis,
                    notes=notes,
                    status=Prescription.Status.CREATED,
                )
                for idx, (item_idx, _) in enumerate(valid):
                    med_id = med_ids_list[item_idx] if item_idx < len(med_ids_list) else ''
                    med_obj = None
                    if med_id:
                        try:
                            med_obj = Medication.objects.get(pk=med_id, is_active=True)
                        except Medication.DoesNotExist:
                            pass

                    d_name = drug_names[item_idx].strip()
                    if med_obj and not d_name:
                        d_name = f"{med_obj.name} {med_obj.strength}"

                    PrescriptionItem.objects.create(
                        prescription=rx,
                        medication=med_obj,
                        drug_name=d_name,
                        dose=doses[item_idx] if item_idx < len(doses) else '',
                        route=routes[item_idx] if item_idx < len(routes) else PrescriptionItem.Route.ORAL,
                        frequency=frequencies[item_idx] if item_idx < len(frequencies) else PrescriptionItem.Frequency.TWICE_DAILY,
                        duration_days=int(durations[item_idx]) if item_idx < len(durations) and durations[item_idx] else 7,
                        quantity=int(quantities[item_idx]) if item_idx < len(quantities) and quantities[item_idx] else 1,
                        meal_instruction=meal_instrs[item_idx] if item_idx < len(meal_instrs) else '',
                        special_instructions=specials[item_idx] if item_idx < len(specials) else '',
                        order_index=idx,
                    )

            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.PHARMACY,
                object_type='Prescription', object_id=rx.pk,
                object_repr=f'{rx.prescription_number} — {patient.full_name}',
                description=f'Prescription {rx.prescription_number} created for {patient.full_name} ({rx.items.count()} item(s))',
                extra_data={'items': rx.items.count(), 'diagnosis': diagnosis[:100]},
                request=request,
            )
            action = request.POST.get('action', 'save')
            if action == 'send':
                rx.status = Prescription.Status.SENT
                rx.sent_to_pharmacy_at = timezone.now()
                rx.save(update_fields=['status', 'sent_to_pharmacy_at'])
                messages.success(request, f'Prescription {rx.prescription_number} created and sent to pharmacy.')
            else:
                messages.success(request, f'Prescription {rx.prescription_number} created.')
            return redirect('prescription_detail', rx_id=rx.pk)
        except Exception as exc:
            messages.error(request, f'Error creating prescription: {exc}')

    return _render_create(request, visit, last_dx, '', '')


def _render_create(request, visit, last_dx, diagnosis, notes):
    return render(request, 'prescription/create.html', {
        'visit': visit,
        'patient': visit.patient,
        'last_dx': last_dx,
        'prefill_diagnosis': diagnosis or (last_dx.description[:200] if last_dx else ''),
        'notes': notes,
        'routes': PrescriptionItem.Route.choices,
        'frequencies': PrescriptionItem.Frequency.choices,
        'meal_instructions': PrescriptionItem.Instructions.choices,
    })


@hms_permission_required('core.read_prescription')
def prescription_detail(request, rx_id):
    rx = _get_rx(rx_id)
    # batch info for each item
    batch_map: dict[int, list] = {}
    med_ids = [item.medication_id for item in rx.items.all() if item.medication_id]
    if med_ids:
        for b in (MedicationBatch.objects
                  .filter(medication_id__in=med_ids, is_active=True, quantity_available__gt=0)
                  .select_related('medication')
                  .order_by('expiration_date')):
            batch_map.setdefault(b.medication_id, []).append(b)

    return render(request, 'prescription/detail.html', {
        'rx': rx,
        'batch_map': batch_map,
        'can_send': (request.user.has_perm('core.write_prescription')
                     and rx.status == Prescription.Status.CREATED),
        'can_cancel': (request.user == rx.prescribed_by
                       and rx.status in [Prescription.Status.CREATED, Prescription.Status.SENT]),
    })


@hms_permission_required('core.write_prescription')
@require_POST
def prescription_send(request, rx_id):
    rx = get_object_or_404(Prescription, pk=rx_id)
    if rx.status != Prescription.Status.CREATED:
        messages.error(request, 'Prescription has already been sent or is not in Created status.')
        return redirect('prescription_detail', rx_id=rx_id)

    rx.status = Prescription.Status.SENT
    rx.sent_to_pharmacy_at = timezone.now()
    rx.save(update_fields=['status', 'sent_to_pharmacy_at'])

    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.PHARMACY,
        object_type='Prescription', object_id=rx.pk,
        object_repr=rx.prescription_number,
        description=f'Prescription {rx.prescription_number} sent to pharmacy for {rx.patient.full_name}',
        request=request,
    )
    messages.success(request, f'Prescription {rx.prescription_number} sent to pharmacy.')
    return redirect('prescription_detail', rx_id=rx_id)


@hms_permission_required('core.write_prescription')
@require_POST
def prescription_cancel(request, rx_id):
    rx = get_object_or_404(Prescription, pk=rx_id)
    if rx.status in [Prescription.Status.DISPENSED, Prescription.Status.CANCELLED]:
        messages.error(request, 'Cannot cancel a dispensed or already cancelled prescription.')
        return redirect('prescription_detail', rx_id=rx_id)

    reason = request.POST.get('reason', '').strip()
    rx.status = Prescription.Status.CANCELLED
    rx.rejection_reason = reason
    rx.save(update_fields=['status', 'rejection_reason'])
    rx.items.filter(status=PrescriptionItem.Status.PENDING).update(
        status=PrescriptionItem.Status.CANCELLED,
    )

    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.PHARMACY,
        object_type='Prescription', object_id=rx.pk,
        object_repr=rx.prescription_number,
        description=f'Prescription {rx.prescription_number} cancelled. Reason: {reason or "—"}',
        extra_data={'reason': reason},
        request=request,
    )
    messages.success(request, 'Prescription cancelled.')
    return redirect('prescription_detail', rx_id=rx_id)


@hms_permission_required('core.read_prescription')
def prescription_history(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    status_filter = request.GET.get('status', '')

    qs = (
        Prescription.objects
        .filter(patient=patient)
        .select_related('prescribed_by', 'visit__department')
        .prefetch_related('items')
        .order_by('-created_at')
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'prescription/history.html', {
        'patient': patient,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'statuses': Prescription.Status.choices,
    })


# ═══════════════════════════════════════════════════════════════════════
# PHARMACY SIDE
# ═══════════════════════════════════════════════════════════════════════

@hms_permission_required('core.read_prescription_for_dispensing')
def rx_queue(request):
    status_filter = request.GET.get('status', 'active')
    search = request.GET.get('q', '').strip()

    qs = (
        Prescription.objects
        .select_related('patient', 'visit__department', 'prescribed_by')
        .prefetch_related('items')
        .order_by('-sent_to_pharmacy_at', '-created_at')
    )

    if status_filter == 'active':
        qs = qs.filter(status__in=[
            Prescription.Status.SENT,
            Prescription.Status.REVIEWING,
            Prescription.Status.READY,
        ])
    elif status_filter == 'partial':
        qs = qs.filter(status=Prescription.Status.PARTIAL)
    elif status_filter == 'dispensed':
        qs = qs.filter(status=Prescription.Status.DISPENSED)
    elif status_filter == 'all':
        pass

    if search:
        qs = qs.filter(
            Q(prescription_number__icontains=search)
            | Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__card_number__icontains=search)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    pending_count = Prescription.objects.filter(
        status__in=[Prescription.Status.SENT, Prescription.Status.REVIEWING]
    ).count()

    return render(request, 'pharmacy/rx_queue.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'search': search,
        'pending_count': pending_count,
    })


@hms_permission_required('core.read_prescription_for_dispensing')
def rx_detail(request, rx_id):
    rx = _get_rx(rx_id)

    # Stock availability for each item
    stock_info: dict[int, dict] = {}
    for item in rx.items.all():
        info = {'batches': [], 'total_stock': 0, 'pharmacy_stocks': []}
        if item.medication_id:
            batches = (
                MedicationBatch.objects
                .filter(medication_id=item.medication_id, is_active=True, quantity_available__gt=0)
                .order_by('expiration_date')
            )
            info['batches'] = list(batches)
            info['total_stock'] = sum(b.quantity_available for b in info['batches'])

        # Also check PharmacyStock (legacy) by matching drug name
        ps_qs = PharmacyStock.objects.filter(
            quantity_in_stock__gt=0
        ).filter(
            Q(drug_name__icontains=item.drug_name[:20])
            | Q(generic_name__icontains=item.drug_name[:20])
        )
        info['pharmacy_stocks'] = list(ps_qs[:5])
        stock_info[item.pk] = info

    has_billed_items = rx.items.filter(status=PrescriptionItem.Status.BILLED).exists()

    return render(request, 'pharmacy/rx_detail.html', {
        'rx': rx,
        'stock_info': stock_info,
        'can_verify': request.user.has_perm('core.dispense_medication'),
        'can_dispense': request.user.has_perm('core.dispense_medication'),
        'has_billed_items': has_billed_items,
    })


@hms_permission_required('core.dispense_medication')
@require_POST
def rx_verify(request, rx_id):
    rx = get_object_or_404(Prescription, pk=rx_id)
    action = request.POST.get('action', '')
    reason = request.POST.get('reason', '').strip()

    if action == 'approve':
        rx.status = Prescription.Status.READY
        rx.verified_by = request.user
        rx.verified_at = timezone.now()
        rx.rejection_reason = ''
        rx.save(update_fields=['status', 'verified_by', 'verified_at', 'rejection_reason', 'updated_at'])
        log_action(
            request.user, AuditLog.Action.APPROVE, AuditLog.Module.PHARMACY,
            object_type='Prescription', object_id=rx.pk,
            object_repr=rx.prescription_number,
            description=f'Prescription {rx.prescription_number} verified and approved for {rx.patient.full_name}',
            request=request,
        )
        messages.success(request, f'Prescription {rx.prescription_number} approved — ready to dispense.')
        return redirect('rx_dispense', rx_id=rx_id)

    elif action == 'reject':
        if not reason:
            messages.error(request, 'A reason is required to reject a prescription.')
            return redirect('rx_detail', rx_id=rx_id)
        rx.status = Prescription.Status.CANCELLED
        rx.rejection_reason = reason
        rx.verified_by = request.user
        rx.verified_at = timezone.now()
        rx.save(update_fields=['status', 'rejection_reason', 'verified_by', 'verified_at', 'updated_at'])
        rx.items.filter(status=PrescriptionItem.Status.PENDING).update(
            status=PrescriptionItem.Status.CANCELLED,
        )
        log_action(
            request.user, AuditLog.Action.REJECT, AuditLog.Module.PHARMACY,
            object_type='Prescription', object_id=rx.pk,
            object_repr=rx.prescription_number,
            description=f'Prescription {rx.prescription_number} rejected. Reason: {reason}',
            extra_data={'reason': reason},
            request=request,
        )
        messages.warning(request, f'Prescription {rx.prescription_number} rejected.')
        return redirect('rx_queue')

    elif action == 'clarify':
        # Put it back to reviewing status; doctor sees 'Under Review'
        rx.status = Prescription.Status.REVIEWING
        rx.rejection_reason = reason
        rx.save(update_fields=['status', 'rejection_reason', 'updated_at'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.PHARMACY,
            object_type='Prescription', object_id=rx.pk,
            object_repr=rx.prescription_number,
            description=f'Clarification requested for prescription {rx.prescription_number}: {reason}',
            extra_data={'note': reason},
            request=request,
        )
        messages.info(request, 'Clarification request sent to prescribing doctor.')
        return redirect('rx_detail', rx_id=rx_id)

    messages.error(request, 'Unknown action.')
    return redirect('rx_detail', rx_id=rx_id)


@hms_permission_required('core.dispense_medication')
def rx_dispense(request, rx_id):
    rx = _get_rx(rx_id)

    if rx.status not in [Prescription.Status.READY, Prescription.Status.PARTIAL,
                          Prescription.Status.REVIEWING, Prescription.Status.SENT]:
        messages.error(request, f'Prescription is {rx.status} and cannot be dispensed.')
        return redirect('rx_detail', rx_id=rx_id)

    # Build stock options per item
    pending_items = rx.items.filter(
        status__in=[PrescriptionItem.Status.PENDING, PrescriptionItem.Status.PARTIAL]
    )

    stock_options: dict[int, dict] = {}
    for item in pending_items:
        batches = []
        if item.medication_id:
            batches = list(
                MedicationBatch.objects
                .filter(medication_id=item.medication_id, is_active=True, quantity_available__gt=0)
                .order_by('expiration_date')
            )
        ps_list = list(
            PharmacyStock.objects.filter(
                quantity_in_stock__gt=0
            ).filter(
                Q(drug_name__icontains=item.drug_name[:20])
                | Q(generic_name__icontains=item.drug_name[:20])
            )[:5]
        )
        stock_options[item.pk] = {
            'batches': batches,
            'pharmacy_stocks': ps_list,
            'total_batch_stock': sum(b.quantity_available for b in batches),
        }

    if request.method == 'POST':
        errors = []
        dispense_rows = []

        for item in pending_items:
            qty_key = f'qty_{item.pk}'
            src_key = f'src_{item.pk}'
            price_key = f'price_{item.pk}'
            batch_key = f'batch_{item.pk}'
            notes_key = f'notes_{item.pk}'

            qty_str = request.POST.get(qty_key, '').strip()
            if not qty_str:
                continue
            try:
                qty = int(qty_str)
            except ValueError:
                errors.append(f'Invalid quantity for {item.drug_name}')
                continue
            if qty <= 0:
                continue

            src = request.POST.get(src_key, '').strip()  # 'batch:<id>' or 'ps:<id>'
            price_str = request.POST.get(price_key, '0').strip()
            manual_batch = request.POST.get(batch_key, '').strip()
            notes_val = request.POST.get(notes_key, '').strip()

            try:
                unit_price = Decimal(price_str)
            except Exception:
                unit_price = Decimal('0')

            dispense_rows.append({
                'item': item,
                'qty': qty,
                'src': src,
                'unit_price': unit_price,
                'manual_batch': manual_batch,
                'notes': notes_val,
            })

        if errors:
            for e in errors:
                messages.error(request, e)
        elif not dispense_rows:
            messages.error(request, 'No quantities entered.')
        else:
            try:
                with transaction.atomic():
                    # Send charges to billing — stock NOT deducted until payment confirmed
                    invoice = None
                    for row in dispense_rows:
                        item = row['item']
                        qty = row['qty']
                        src = row['src']
                        unit_price = row['unit_price']

                        batch_obj = None
                        ps_obj = None
                        batch_num = row['manual_batch']
                        expiry_date = None

                        if src.startswith('batch:'):
                            b_id = int(src.split(':')[1])
                            batch_obj = MedicationBatch.objects.get(pk=b_id)
                            batch_num = batch_obj.batch_number
                            expiry_date = batch_obj.expiration_date
                            if not unit_price:
                                unit_price = item.medication.selling_price if item.medication else Decimal('0')
                            if batch_obj.quantity_available < qty:
                                raise ValueError(
                                    f'Insufficient stock for {item.drug_name}: '
                                    f'need {qty}, have {batch_obj.quantity_available}'
                                )

                        elif src.startswith('ps:'):
                            ps_id = int(src.split(':')[1])
                            ps_obj = PharmacyStock.objects.get(pk=ps_id)
                            batch_num = batch_num or ps_obj.batch_number
                            expiry_date = ps_obj.expiry_date
                            if not unit_price:
                                unit_price = ps_obj.selling_price
                            if ps_obj.quantity_in_stock < qty:
                                raise ValueError(
                                    f'Insufficient stock for {item.drug_name}: '
                                    f'need {qty}, have {ps_obj.quantity_in_stock}'
                                )

                        # Record dispense intent — stock_confirmed=False (pending payment)
                        RxDispenseRecord.objects.create(
                            prescription_item=item,
                            medication_batch=batch_obj,
                            pharmacy_stock=ps_obj,
                            quantity_dispensed=qty,
                            unit_price=unit_price,
                            dispensed_by=request.user,
                            batch_number=batch_num,
                            expiry_date=expiry_date,
                            notes=row['notes'],
                            stock_confirmed=False,
                        )

                        item.quantity_dispensed += qty
                        item.status = PrescriptionItem.Status.BILLED
                        item.save(update_fields=['quantity_dispensed', 'status'])

                        # Add charges to visit invoice
                        if unit_price > 0 and rx.visit_id:
                            if invoice is None:
                                invoice = Invoice.objects.filter(
                                    visit=rx.visit,
                                    status__in=['Draft', 'Issued'],
                                ).first()
                                if not invoice:
                                    invoice = Invoice.objects.create(
                                        patient=rx.patient,
                                        visit=rx.visit,
                                        created_by=request.user,
                                        status='Draft',
                                        payment_type='Cash',
                                        total_amount=Decimal('0'),
                                    )
                            item_desc = (
                                f"{item.drug_name} ({item.dose}, {item.get_frequency_display()}) "
                                f"— Rx {rx.prescription_number}"
                            )
                            InvoiceItem.objects.create(
                                invoice=invoice,
                                description=item_desc[:255],
                                service_type='Medication',
                                quantity=qty,
                                unit_price=unit_price,
                                total=unit_price * qty,
                            )
                            invoice.total_amount = (
                                invoice.items.aggregate(t=Sum('total'))['t'] or Decimal('0')
                            )
                            invoice.save(update_fields=['total_amount', 'updated_at'])

                    # Prescription stays Ready — charges billed, awaiting payment
                    rx.status = Prescription.Status.READY
                    rx.save(update_fields=['status', 'updated_at'])

                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
                    object_type='Prescription', object_id=rx.pk,
                    object_repr=rx.prescription_number,
                    description=(
                        f'Medication charges sent to billing for Rx {rx.prescription_number} '
                        f'— {rx.patient.full_name} ({len(dispense_rows)} item(s))'
                    ),
                    request=request,
                )
                messages.success(
                    request,
                    f'{len(dispense_rows)} medication charge(s) added to invoice. '
                    f'Patient pays at billing, then confirm dispensing here.'
                )
                return redirect('rx_detail', rx_id=rx_id)

            except Exception as exc:
                messages.error(request, f'Error sending charges: {exc}')

    return render(request, 'pharmacy/rx_dispense.html', {
        'rx': rx,
        'pending_items': pending_items,
        'stock_options': stock_options,
    })


@hms_permission_required('core.read_prescription_for_dispensing')
def rx_print(request, rx_id):
    rx = _get_rx(rx_id)
    return render(request, 'pharmacy/rx_print.html', {'rx': rx})


@hms_permission_required('core.dispense_medication')
@require_POST
def rx_confirm_dispense(request, rx_id):
    """Physically release medications and deduct inventory after payment is confirmed."""
    rx = _get_rx(rx_id)

    # Find the visit invoice and verify payment
    invoice = None
    if rx.visit_id:
        invoice = Invoice.objects.filter(
            visit=rx.visit,
            status__in=['Paid', 'Overpaid', 'Waived', 'Partial', 'Credit Pending'],
        ).first()

    if not invoice:
        messages.error(
            request,
            'Cannot confirm dispensing: no paid invoice found for this visit. '
            'Please ensure the patient has paid at the billing counter.'
        )
        return redirect('rx_detail', rx_id=rx_id)

    pending_records = RxDispenseRecord.objects.filter(
        prescription_item__prescription=rx,
        stock_confirmed=False,
    ).select_related('medication_batch', 'pharmacy_stock', 'prescription_item')

    if not pending_records.exists():
        messages.info(request, 'No pending dispense records found — medications may already be dispensed.')
        return redirect('rx_detail', rx_id=rx_id)

    try:
        with transaction.atomic():
            for rec in pending_records.select_for_update():
                qty = rec.quantity_dispensed
                if rec.medication_batch_id:
                    b = rec.medication_batch
                    if b.quantity_available < qty:
                        raise ValueError(
                            f'Insufficient stock for {rec.prescription_item.drug_name}: '
                            f'need {qty}, available {b.quantity_available}'
                        )
                    b.quantity_available -= qty
                    b.save(update_fields=['quantity_available'])

                elif rec.pharmacy_stock_id:
                    s = rec.pharmacy_stock
                    if s.quantity_in_stock < qty:
                        raise ValueError(
                            f'Insufficient stock for {rec.prescription_item.drug_name}: '
                            f'need {qty}, available {s.quantity_in_stock}'
                        )
                    s.quantity_in_stock -= qty
                    s.save(update_fields=['quantity_in_stock'])

                rec.stock_confirmed = True
                rec.confirmed_by = request.user
                rec.confirmed_at = timezone.now()
                rec.save(update_fields=['stock_confirmed', 'confirmed_by', 'confirmed_at'])

                item = rec.prescription_item
                if item.quantity_dispensed >= item.quantity:
                    item.status = PrescriptionItem.Status.DISPENSED
                else:
                    item.status = PrescriptionItem.Status.PARTIAL
                item.save(update_fields=['status'])

            # Update prescription overall status
            all_items = list(rx.items.all())
            statuses = {i.status for i in all_items}
            if all(s == PrescriptionItem.Status.DISPENSED for s in statuses):
                rx.status = Prescription.Status.DISPENSED
            elif all(s == PrescriptionItem.Status.CANCELLED for s in statuses):
                rx.status = Prescription.Status.CANCELLED
            elif PrescriptionItem.Status.DISPENSED in statuses or PrescriptionItem.Status.PARTIAL in statuses:
                rx.status = Prescription.Status.PARTIAL
            rx.save(update_fields=['status', 'updated_at'])

        log_action(
            request.user, AuditLog.Action.DISPENSE, AuditLog.Module.PHARMACY,
            object_type='Prescription', object_id=rx.pk,
            object_repr=rx.prescription_number,
            description=(
                f'Medications dispensed for Rx {rx.prescription_number} '
                f'— {rx.patient.full_name} (payment confirmed, inventory deducted)'
            ),
            request=request,
        )
        messages.success(
            request,
            f'Medications dispensed for {rx.prescription_number}. Inventory updated.'
        )
    except Exception as exc:
        messages.error(request, f'Error confirming dispense: {exc}')

    return redirect('rx_detail', rx_id=rx_id)


# ── MAR (Medication Administration Record) ────────────────────────────────────

@hms_permission_required('core.record_vital_signs')
def mar_list(request, visit_id):
    visit = _get_visit(visit_id)
    entries = (
        MAREntry.objects
        .filter(visit=visit)
        .select_related('prescription_item', 'administered_by')
        .order_by('scheduled_time')
    )
    return render(request, 'prescription/mar.html', {
        'visit': visit,
        'entries': entries,
        'statuses': MAREntry.Status.choices,
    })


@hms_permission_required('core.record_vital_signs')
@require_POST
def mar_update(request, entry_id):
    entry = get_object_or_404(MAREntry, pk=entry_id)
    status = request.POST.get('status', '')
    dose_given = request.POST.get('dose_given', '').strip()
    notes = request.POST.get('notes', '').strip()
    patient_response = request.POST.get('patient_response', '').strip()
    reason_missed = request.POST.get('reason_missed', '').strip()

    if status in dict(MAREntry.Status.choices):
        entry.status = status
        if status == MAREntry.Status.GIVEN:
            entry.administered_at = timezone.now()
            entry.administered_by = request.user
            entry.dose_given = dose_given
        entry.patient_response = patient_response
        entry.reason_missed = reason_missed
        entry.notes = notes
        entry.save()
        messages.success(request, 'MAR entry updated.')
    else:
        messages.error(request, 'Invalid status.')

    return redirect('mar_list', visit_id=entry.visit_id)
