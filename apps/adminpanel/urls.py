from django.urls import path

from . import views

app_name = "adminpanel"

urlpatterns = [
    path("", views.dashboard_home, name="dashboard"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/activate/", views.user_activate, name="user_activate"),
    path("users/<int:pk>/suspend/", views.user_suspend, name="user_suspend"),
    path("users/<int:pk>/deactivate/", views.user_deactivate, name="user_deactivate"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),
    path("audit-log/", views.audit_log, name="audit_log"),
    path("settings/", views.site_settings, name="site_settings"),
    path("health/", views.system_health, name="system_health"),
    path("backups/", views.backups_view, name="backups"),
    path("backups/trigger/", views.backups_trigger, name="backups_trigger"),
    path("feedback/", views.feedback_list, name="feedback_list"),
    path("feedback/<int:pk>/", views.feedback_detail, name="feedback_detail"),
]
