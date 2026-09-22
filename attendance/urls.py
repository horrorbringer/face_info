from django.urls import path
from . import views

urlpatterns = [
    # Auth & Profile
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("students/me/", views.StudentMeView.as_view(), name="students-me"),

    # Attendance & QR
    path("attendance/checkin/qr/", views.QRCheckInView.as_view(), name="attendance-checkin-qr"),
    path("attendance/checkin/face/", views.FaceCheckInView.as_view(), name="attendance-checkin-face"),
    path("attendance/history/", views.StudentAttendanceHistoryView.as_view(), name="attendance-history"),

    # Face Enrollment
    path("face/enroll/", views.FaceEnrollView.as_view(), name="face-enroll"),

    # Teacher Endpoints
    path("teacher/classes/today/", views.TeacherTodayClassesView.as_view(), name="teacher-classes-today"),
    path("teacher/sessions/<int:session_id>/qr/", views.SessionRotateQRView.as_view(), name="teacher-session-qr"),
    path("teacher/sessions/<int:session_id>/qr/dynamic/", views.SessionDynamicQRView.as_view(), name="teacher-session-qr-dynamic"),
    path("teacher/sessions/<int:session_id>/live-qr/", views.session_live_qr, name="teacher-session-live-qr"),
    path("teacher/sessions/<int:session_id>/end/", views.SessionEndView.as_view(), name="teacher-session-end"),
    path("teacher/attendance/<int:record_id>/", views.AttendanceOverrideView.as_view(), name="teacher-attendance-override"),

    # Reports
    path("reports/class/<int:class_id>/", views.ClassReportView.as_view(), name="reports-class"),
    path("reports/student/<int:student_id>/", views.StudentReportView.as_view(), name="reports-student"),
]
