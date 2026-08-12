from django.conf import settings
from django.db import models


class LookupAuditLog(models.Model):
    OUTCOMES = [("matched", "Matched"), ("no_match", "No match"), ("review", "Needs review"), ("manual", "Manual lookup")]
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.SET_NULL)
    outcome = models.CharField(max_length=16, choices=OUTCOMES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

