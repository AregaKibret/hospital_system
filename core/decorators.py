from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def hms_permission_required(*perms):
    """
    Decorator that requires the user to be logged in AND have ALL listed perms.
    Usage:
        @hms_permission_required('core.add_patient')
        def my_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if all(request.user.has_perm(p) for p in perms):
                return view_func(request, *args, **kwargs)
            return redirect('access_denied')
        return _wrapped
    return decorator
