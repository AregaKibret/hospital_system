from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    # Django's built-in admin at the conventional "admin/" prefix. The HMS
    # app's own settings/configuration pages (Role/Department/Card-Type/
    # Specialization management, etc.) live under "settings/..." in
    # core.urls instead, so the two no longer collide.
    path('admin/', admin.site.urls),

    # Built-in auth views (login / logout / password change)
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(template_name='accounts/login.html'),
        name='login',
    ),
    path(
        'accounts/logout/',
        auth_views.LogoutView.as_view(next_page='login'),
        name='logout',
    ),
    path(
        'accounts/password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='accounts/password_change.html',
            success_url='/accounts/password-change/done/',
        ),
        name='password_change',
    ),
    path(
        'accounts/password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='accounts/password_change_done.html',
        ),
        name='password_change_done',
    ),

    # HMS core app
    path('', include('core.urls')),
]
