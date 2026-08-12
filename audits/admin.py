from django.contrib import admin
from .models import LookupAuditLog


@admin.register(LookupAuditLog)
class LookupAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "staff_user", "student", "outcome")
    readonly_fields = ("created_at", "staff_user", "student", "outcome")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False

