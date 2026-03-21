from django.http import HttpResponseForbidden
from django.shortcuts import redirect


# Paths that are always accessible (public pages, auth, static, API)
PUBLIC_PREFIXES = (
    '/', '/signup/', '/verify/', '/auth/', '/about/', '/privacy/', '/terms/',
    '/industries/', '/find/', '/pro/', '/demo/', '/sitemap.xml', '/robots.txt',
    '/google', '/static/', '/media/', '/api/', '/favicon',
)

# Paths restricted to staff/admin only
ADMIN_PREFIXES = (
    '/admin-leads/', '/admin/', '/monitors/',
)

# Paths restricted to salespeople + staff
SALES_PREFIXES = (
    '/sales/',
)

# Paths restricted to customers + staff (the main dashboard)
CUSTOMER_PREFIXES = (
    '/dashboard/', '/onboarding/', '/campaigns/', '/analytics/',
    '/territory/', '/crm/',
)


class RoleAccessMiddleware:
    """
    Enforce role-based URL access:
    - Admin/staff can access everything
    - Salespeople can only access /sales/ URLs
    - Customers can only access /dashboard/, /campaigns/, etc.
    - Public pages are accessible to everyone
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Skip for unauthenticated users (they'll hit @login_required)
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Admin/staff can access everything
        if request.user.is_superuser or request.user.is_staff:
            return self.get_response(request)

        # Check if path is public
        if self._is_public(path):
            return self.get_response(request)

        is_salesperson = hasattr(request.user, 'salesperson_profile')
        is_customer = hasattr(request.user, 'business_profile')

        # Salespeople blocked from admin and customer areas
        if is_salesperson and not is_customer:
            if self._matches_any(path, ADMIN_PREFIXES):
                return HttpResponseForbidden('Access denied.')
            if self._matches_any(path, CUSTOMER_PREFIXES):
                return HttpResponseForbidden('Access denied.')

        # Customers blocked from admin and sales areas
        if is_customer and not is_salesperson:
            if self._matches_any(path, ADMIN_PREFIXES):
                return HttpResponseForbidden('Access denied.')
            if self._matches_any(path, SALES_PREFIXES):
                return HttpResponseForbidden('Access denied.')

        return self.get_response(request)

    def _is_public(self, path):
        # Exact match for homepage
        if path == '/':
            return True
        for prefix in PUBLIC_PREFIXES:
            if prefix != '/' and path.startswith(prefix):
                return True
        return False

    def _matches_any(self, path, prefixes):
        return any(path.startswith(p) for p in prefixes)
