from .permissions import ROLE_META, ROLE_NAMES


def user_role(request):
    """Injects role display info into every template context."""
    if not request.user.is_authenticated:
        return {'user_role_name': '', 'user_role_badge': '', 'user_role_desc': ''}

    # groups is prefetched by Django's auth middleware for permission checks,
    # so .first() hits the prefetch cache rather than issuing a new query.
    group = request.user.groups.first()
    role_name = group.name if group else ('Administrator' if request.user.is_superuser else '')
    meta = ROLE_META.get(role_name, {'badge': 'bg-gray-100 text-gray-700', 'desc': ''})

    return {
        'user_role_name': role_name,
        'user_role_badge': meta['badge'],
        'user_role_desc': meta['desc'],
        'all_role_names': ROLE_NAMES,
    }
