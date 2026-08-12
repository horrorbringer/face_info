from django.urls import path
from . import views

app_name = "recognition"
urlpatterns = [path("", views.kiosk, name="kiosk"), path("confirm/<str:student_id>/", views.confirm, name="confirm")]
