from django.urls import path
from . import views

urlpatterns = [
    # System & Discovery
    path("", views.ApiRootView.as_view(), name="api-root"),
    path("health/", views.HealthCheckView.as_view(), name="api-health"),
    path("schema/", views.api_schema_view, name="api-schema"),
    path("docs/", views.api_docs_view, name="api-docs"),

    # Auth & Profile
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/change-password/", views.ChangePasswordView.as_view(), name="auth-change-password"),
    path("students/me/", views.StudentMeView.as_view(), name="students-me"),

    # Attendance & QR
    path("attendance/checkin/qr/", views.QRCheckInView.as_view(), name="attendance-checkin-qr"),
    path("attendance/checkin/face/", views.FaceCheckInView.as_view(), name="attendance-checkin-face"),
    path("attendance/history/", views.StudentAttendanceHistoryView.as_view(), name="attendance-history"),

    # Face Enrollment
    path("face/enroll/", views.FaceEnrollView.as_view(), name="face-enroll"),

    # Teacher Endpoints
    path("teacher/classes/today/", views.TeacherTodayClassesView.as_view(), name="teacher-classes-today"),
    path("teacher/sessions/", views.TeacherSessionCreateView.as_view(), name="teacher-session-create"),
    path("teacher/sessions/<int:session_id>/roster/", views.TeacherSessionRosterView.as_view(), name="teacher-session-roster"),
    path("teacher/sessions/<int:session_id>/qr/", views.SessionRotateQRView.as_view(), name="teacher-session-qr"),
    path("teacher/sessions/<int:session_id>/qr/dynamic/", views.SessionDynamicQRView.as_view(), name="teacher-session-qr-dynamic"),
    path("teacher/sessions/<int:session_id>/live-qr/", views.session_live_qr, name="teacher-session-live-qr"),
    path("teacher/sessions/<int:session_id>/end/", views.SessionEndView.as_view(), name="teacher-session-end"),
    path("teacher/attendance/<int:record_id>/", views.AttendanceOverrideView.as_view(), name="teacher-attendance-override"),

    # Reports
    path("reports/class/<int:class_id>/", views.ClassReportView.as_view(), name="reports-class"),
    path("reports/student/<int:student_id>/", views.StudentReportView.as_view(), name="reports-student"),
]
