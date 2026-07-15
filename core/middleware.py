import re
from datetime import timedelta

from django.contrib.auth import logout
from django.utils import timezone

from core.audit import set_current_request

SESSION_TIMEOUT_MINUTES = 120  # 2 hours


class AuditMiddleware:
    """Store the current request in thread-local so log_action() can read IP/UA without request."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        response = self.get_response(request)
        set_current_request(None)
        return response


class SessionActivityMiddleware:
    """
    - Updates UserSession.last_activity on every authenticated request.
    - Logs out users idle for SESSION_TIMEOUT_MINUTES.
    - Skips static files, media, and AJAX polling endpoints.
    """
    SKIP_PATHS = re.compile(
        r'^/(static|media|favicon|__debug__|api/notifications/unread-count)/'
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.user.is_authenticated
                and not self.SKIP_PATHS.match(request.path)):
            self._update_session(request)
        return self.get_response(request)

    def _update_session(self, request):
        try:
            from core.models import UserSession
            session_key = request.session.session_key or ''
            now = timezone.now()
            cutoff = now - timedelta(minutes=SESSION_TIMEOUT_MINUTES)

            sess = UserSession.objects.filter(
                session_key=session_key, is_active=True
            ).first()

            if sess:
                if sess.last_activity < cutoff:
                    # Timed out
                    sess.is_active = False
                    sess.logout_at = now
                    sess.logout_type = 'timeout'
                    sess.save(update_fields=['is_active', 'logout_at', 'logout_type'])
                    logout(request)
                else:
                    sess.last_activity = now
                    sess.save(update_fields=['last_activity'])
        except Exception:
            pass
