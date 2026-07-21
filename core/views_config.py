"""
Configuration views — Hospital Profile, Module Management, System Version/About.
All require Administrator access.
"""
from django.contrib import messages
from django.shortcuts import redirect, render

from .audit import log_action
from .models import HospitalProfile, LicenseInfo, SystemModule, SystemVersion
from .decorators import hms_permission_required


# ── Hospital Profile ──────────────────────────────────────────────────────────

@hms_permission_required('core.manage_system_config')
def hospital_profile(request):
    profile = HospitalProfile.get()

    if request.method == 'POST':
        p = request.POST
        profile.name           = p.get('name', profile.name).strip()
        profile.short_name     = p.get('short_name', '').strip()
        profile.tagline        = p.get('tagline', '').strip()
        profile.address        = p.get('address', '').strip()
        profile.city           = p.get('city', '').strip()
        profile.region         = p.get('region', '').strip()
        profile.country        = p.get('country', 'Ethiopia').strip()
        profile.phone          = p.get('phone', '').strip()
        profile.phone_alt      = p.get('phone_alt', '').strip()
        profile.email          = p.get('email', '').strip()
        profile.website        = p.get('website', '').strip()
        profile.license_number = p.get('license_number', '').strip()
        profile.tin            = p.get('tin', '').strip()
        profile.currency       = p.get('currency', 'ETB').strip()
        profile.currency_symbol = p.get('currency_symbol', 'ETB').strip()
        profile.timezone       = p.get('timezone', 'Africa/Addis_Ababa').strip()
        profile.language       = p.get('language', 'en').strip()
        profile.report_header  = p.get('report_header', '').strip()
        profile.report_footer  = p.get('report_footer', '').strip()
        profile.primary_color  = p.get('primary_color', '#2563eb').strip()

        # Handle file uploads
        if 'logo' in request.FILES:
            profile.logo = request.FILES['logo']
        if 'stamp' in request.FILES:
            profile.stamp = request.FILES['stamp']

        # Clear logo/stamp if requested
        if p.get('clear_logo'):
            profile.logo = None
        if p.get('clear_stamp'):
            profile.stamp = None

        profile.save()
        log_action(request.user, 'UPDATE', 'Config', 'HospitalProfile', profile.pk, str(profile),
                   description='Hospital profile updated', request=request)
        messages.success(request, 'Hospital profile saved successfully.')
        return redirect('hospital_profile')

    import pytz
    timezones = pytz.all_timezones

    return render(request, 'config/hospital_profile.html', {
        'profile': profile,
        'timezones': timezones,
    })


# ── Module Management ─────────────────────────────────────────────────────────

@hms_permission_required('core.manage_system_config')
def module_management(request):
    modules = SystemModule.objects.all()

    if request.method == 'POST':
        enabled_names = set(request.POST.getlist('enabled'))
        updated = 0
        for mod in modules:
            if mod.is_core:
                continue
            new_state = mod.name in enabled_names
            if mod.is_enabled != new_state:
                mod.is_enabled = new_state
                mod.save(update_fields=['is_enabled'])
                updated += 1
        log_action(request.user, 'UPDATE', 'Config', 'SystemModule', 0, 'Module settings',
                   description=f'Module configuration updated ({updated} changed)', request=request)
        messages.success(request, f'Module settings saved. {updated} module(s) changed.')
        return redirect('module_management')

    by_category = {}
    for mod in modules:
        cat = mod.get_category_display()
        by_category.setdefault(cat, []).append(mod)

    return render(request, 'config/modules.html', {
        'by_category': by_category,
        'modules': modules,
    })


# ── System Version / About ────────────────────────────────────────────────────

def system_about(request):
    import django, sys
    current  = SystemVersion.objects.filter(is_current=True).first()
    history  = SystemVersion.objects.all()[:20]
    license_ = LicenseInfo.objects.first()
    profile  = HospitalProfile.get()

    tech_stack = [
        ('Framework',   f'Django {django.get_version()}'),
        ('Python',      f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'),
        ('Database',    'PostgreSQL 18'),
        ('UI',          'Tailwind CSS'),
        ('Server',      'Gunicorn / WhiteNoise'),
    ]

    return render(request, 'config/about.html', {
        'current_version': current,
        'version_history': history,
        'license': license_,
        'profile': profile,
        'tech_stack': tech_stack,
    })


@hms_permission_required('core.manage_system_config')
def version_history(request):
    versions = SystemVersion.objects.all()
    return render(request, 'config/version_history.html', {
        'versions': versions,
    })


@hms_permission_required('core.manage_system_config')
def license_info(request):
    license_ = LicenseInfo.objects.first()
    return render(request, 'config/license.html', {
        'license': license_,
    })
