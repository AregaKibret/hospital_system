from core.audit import set_current_request


class AuditMiddleware:
    """Store the current request in thread-local so log_action() can
    read IP address / user-agent without needing the request passed explicitly."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        response = self.get_response(request)
        set_current_request(None)
        return response
