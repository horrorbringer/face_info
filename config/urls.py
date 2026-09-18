from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("recognition.urls")),
    path("students/", include("students.urls")),
    path("api/", include("attendance.urls")),
]

