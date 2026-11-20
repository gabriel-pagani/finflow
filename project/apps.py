from django.contrib.admin.apps import AdminConfig


class OTPAdminConfig(AdminConfig):
    default_site = 'project.admin.AdminSite'
