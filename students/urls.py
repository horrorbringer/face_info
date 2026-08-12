from django.urls import path
from . import views

app_name = "students"
urlpatterns = [
    path("lookup/", views.manual_lookup, name="manual_lookup"),
    path("import/", views.import_students, name="import"),
    path("<str:student_id>/enroll/", views.enroll, name="enroll"),
    path("<str:student_id>/revoke/", views.revoke, name="revoke"),
]
