from django import template

register = template.Library()


@register.inclusion_tag('includes/signature_block.html')
def signature_block(user, label='Signature', subtitle=''):
    """Render a name + (optional subtitle) + signature-image-or-"Signature Not
    Available" block for the given user. Used to automatically place an
    employee's uploaded signature on printable documents — the caller
    supplies the verb text (e.g. "Doctor's Signature", "Dispensed By
    Signature", "Verified By Signature") per the document's role context.

    If no explicit subtitle is given and `user` has a linked Doctor profile,
    the doctor's specialization is used as the subtitle automatically.
    """
    signature = None
    display_name = '—'
    computed_subtitle = subtitle or ''

    if user:
        display_name = user.get_full_name() or user.username
        employee = getattr(user, 'employee_profile', None)
        if employee is not None:
            signature = employee.signatures.filter(is_active=True).first()
        if not computed_subtitle:
            doctor = getattr(user, 'doctor_profile', None)
            if doctor is not None and doctor.specialization:
                computed_subtitle = doctor.specialization.name

    return {
        'signature': signature,
        'employee_id': signature.employee_id if signature else None,
        'display_name': display_name,
        'subtitle': computed_subtitle,
        'label': label,
    }
