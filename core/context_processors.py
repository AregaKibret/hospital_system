from .permissions import ROLE_META, ROLE_NAMES


def user_role(request):
    """Injects role display info into every template context."""
    if not request.user.is_authenticated:
        return {'user_role_name': '', 'user_role_badge': '', 'user_role_desc': ''}

    group = request.user.groups.first()
    role_name = group.name if group else ('Administrator' if request.user.is_superuser else '')
    meta = ROLE_META.get(role_name, {'badge': 'bg-gray-100 text-gray-700', 'desc': ''})

    return {
        'user_role_name': role_name,
        'user_role_badge': meta['badge'],
        'user_role_desc': meta['desc'],
        'all_role_names': ROLE_NAMES,
    }


def hospital_context(request):
    """Injects hospital profile, enabled modules, and system version into every template."""
    from .models import HospitalProfile, SystemModule, SystemVersion

    try:
        hospital = HospitalProfile.objects.first() or HospitalProfile()
    except Exception:
        hospital = None

    try:
        modules_qs = list(SystemModule.objects.all())
        enabled_modules = {m.name: m.is_enabled for m in modules_qs}
        all_modules = modules_qs
    except Exception:
        enabled_modules = {}
        all_modules = []

    try:
        version = SystemVersion.objects.filter(is_current=True).first()
    except Exception:
        version = None

    return {
        'hospital': hospital,
        'enabled_modules': enabled_modules,
        'all_modules': all_modules,
        'system_version': version,
    }
