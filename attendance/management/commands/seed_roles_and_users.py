import datetime
import secrets
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token

from attendance.models import (
    AlertLog,
    AttendanceRecord,
    ClassRoom,
    Session,
    StudentFace,
    Teacher,
)
from audits.models import LookupAuditLog
from students.models import FaceEmbedding, Student

User = get_user_model()


class Command(BaseCommand):
    help = "Seed group roles, permissions, classrooms, sessions, and sample user logins for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Reset passwords of existing seed users if they already exist.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("===> Starting Smart Attendance Seed Pipeline..."))
        reset_passwords = options["reset_passwords"]

        # ------------------------------------------------------------------
        # 1. Setup Groups and Permissions
        # ------------------------------------------------------------------
        self.stdout.write("Configuring Groups and Permissions...")

        # Models to permission map
        models_for_perms = [
            ClassRoom,
            Session,
            AttendanceRecord,
            StudentFace,
            AlertLog,
            Student,
            FaceEmbedding,
            LookupAuditLog,
        ]

        perms_dict = {}
        for model in models_for_perms:
            ct = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=ct)
            for p in perms:
                perms_dict[f"{p.content_type.app_label}.{p.codename}"] = p

        def get_perms(codenames):
            found = []
            for name in codenames:
                if name in perms_dict:
                    found.append(perms_dict[name])
                else:
                    self.stdout.write(self.style.WARNING(f"Permission not found: {name}"))
            return found

        # Group 1: Teachers
        teacher_group, _ = Group.objects.get_or_create(name="Teachers")
        teacher_perms = get_perms([
            "attendance.view_classroom",
            "attendance.change_classroom",
            "attendance.view_session",
            "attendance.add_session",
            "attendance.change_session",
            "attendance.view_attendancerecord",
            "attendance.add_attendancerecord",
            "attendance.change_attendancerecord",
            "attendance.view_studentface",
            "attendance.view_alertlog",
            "students.view_student",
            "students.change_student",
            "audits.view_lookupauditlog",
            "audits.add_lookupauditlog",
        ])
        teacher_group.permissions.set(teacher_perms)

        # Group 2: Students
        student_group, _ = Group.objects.get_or_create(name="Students")
        student_perms = get_perms([
            "attendance.view_classroom",
            "attendance.view_session",
            "attendance.view_attendancerecord",
            "students.view_student",
        ])
        student_group.permissions.set(student_perms)

        # Group 3: Kiosk Operators
        kiosk_group, _ = Group.objects.get_or_create(name="Kiosk Operators")
        kiosk_perms = get_perms([
            "students.view_student",
            "students.change_student",
            "attendance.view_studentface",
            "attendance.view_attendancerecord",
            "attendance.add_attendancerecord",
            "audits.add_lookupauditlog",
            "audits.view_lookupauditlog",
        ])
        kiosk_group.permissions.set(kiosk_perms)

        self.stdout.write(self.style.SUCCESS("✓ Groups configured: Teachers, Students, Kiosk Operators"))

        # ------------------------------------------------------------------
        # 2. Seed Users Helper
        # ------------------------------------------------------------------
        created_users = []

        def create_or_update_user(username, password, email, first_name, last_name, is_staff=False, is_superuser=False, group=None):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_staff": is_staff,
                    "is_superuser": is_superuser,
                },
            )
            if created or reset_passwords:
                user.set_password(password)
                user.email = email
                user.first_name = first_name
                user.last_name = last_name
                user.is_staff = is_staff
                user.is_superuser = is_superuser
                user.save()

            if group:
                user.groups.add(group)

            token, _ = Token.objects.get_or_create(user=user)
            created_users.append({
                "username": username,
                "password": password,
                "role": "Superuser" if is_superuser else (group.name if group else "Staff"),
                "email": email,
                "token": token.key,
            })
            return user

        # ------------------------------------------------------------------
        # 3. Seed Superuser & Staff
        # ------------------------------------------------------------------
        admin_user = create_or_update_user(
            username="admin",
            password="AdminPassword123!",
            email="admin@faceinfo.edu",
            first_name="System",
            last_name="Administrator",
            is_staff=True,
            is_superuser=True,
        )

        kiosk_user = create_or_update_user(
            username="kiosk_staff",
            password="KioskPassword123!",
            email="kiosk@faceinfo.edu",
            first_name="Kiosk",
            last_name="Operator",
            is_staff=True,
            group=kiosk_group,
        )

        # ------------------------------------------------------------------
        # 4. Seed Teachers
        # ------------------------------------------------------------------
        user_sokha = create_or_update_user(
            username="teacher_sokha",
            password="TeacherPassword123!",
            email="sokha.chan@faceinfo.edu",
            first_name="Sokha",
            last_name="Chan",
            is_staff=True,
            group=teacher_group,
        )
        teacher_sokha, _ = Teacher.objects.get_or_create(
            user=user_sokha,
            defaults={
                "name": "Mr. Sokha Chan",
                "email": "sokha.chan@faceinfo.edu",
            },
        )

        user_vanny = create_or_update_user(
            username="teacher_vanny",
            password="TeacherPassword123!",
            email="vanny.meas@faceinfo.edu",
            first_name="Vanny",
            last_name="Meas",
            is_staff=True,
            group=teacher_group,
        )
        teacher_vanny, _ = Teacher.objects.get_or_create(
            user=user_vanny,
            defaults={
                "name": "Ms. Vanny Meas",
                "email": "vanny.meas@faceinfo.edu",
            },
        )

        self.stdout.write(self.style.SUCCESS("✓ Teachers seeded: teacher_sokha, teacher_vanny"))

        # ------------------------------------------------------------------
        # 5. Seed ClassRooms
        # ------------------------------------------------------------------
        cs101, _ = ClassRoom.objects.get_or_create(
            name="Computer Science 101",
            defaults={"teacher": teacher_sokha},
        )
        if cs101.teacher != teacher_sokha:
            cs101.teacher = teacher_sokha
            cs101.save()

        se201, _ = ClassRoom.objects.get_or_create(
            name="Software Engineering 201",
            defaults={"teacher": teacher_vanny},
        )
        if se201.teacher != teacher_vanny:
            se201.teacher = teacher_vanny
            se201.save()

        self.stdout.write(self.style.SUCCESS("✓ Classrooms created: CS-101, SE-201"))

        # ------------------------------------------------------------------
        # 6. Seed Students
        # ------------------------------------------------------------------
        student_data = [
            {
                "username": "student_dara",
                "password": "StudentPassword123!",
                "student_id": "STU001",
                "first_name": "Dara",
                "last_name": "Pich",
                "email": "dara.pich@student.faceinfo.edu",
                "class_room": cs101,
                "class_year": "Year 3",
                "guardian_contact": "dara.guardian@gmail.com",
                "consent": True,
            },
            {
                "username": "student_bopha",
                "password": "StudentPassword123!",
                "student_id": "STU002",
                "first_name": "Bopha",
                "last_name": "Keo",
                "email": "bopha.keo@student.faceinfo.edu",
                "class_room": cs101,
                "class_year": "Year 3",
                "guardian_contact": "bopha.guardian@gmail.com",
                "consent": True,
            },
            {
                "username": "student_chenda",
                "password": "StudentPassword123!",
                "student_id": "STU003",
                "first_name": "Chenda",
                "last_name": "Som",
                "email": "chenda.som@student.faceinfo.edu",
                "class_room": se201,
                "class_year": "Year 4",
                "guardian_contact": "chenda.guardian@gmail.com",
                "consent": True,
            },
            {
                "username": "student_rithy",
                "password": "StudentPassword123!",
                "student_id": "STU004",
                "first_name": "Rithy",
                "last_name": "Vuth",
                "email": "rithy.vuth@student.faceinfo.edu",
                "class_room": cs101,
                "class_year": "Year 3",
                "guardian_contact": "rithy.guardian@gmail.com",
                "consent": False,  # Useful for testing pending consent UI
            },
        ]

        now = timezone.now()
        seeded_students = []

        for s in student_data:
            stu_user = create_or_update_user(
                username=s["username"],
                password=s["password"],
                email=s["email"],
                first_name=s["first_name"],
                last_name=s["last_name"],
                group=student_group,
            )
            student, _ = Student.objects.get_or_create(
                student_id=s["student_id"],
                defaults={
                    "user": stu_user,
                    "full_name": f"{s['first_name']} {s['last_name']}",
                    "class_room": s["class_room"],
                    "class_year": s["class_year"],
                    "guardian_contact": s["guardian_contact"],
                    "consent_given_at": now if s["consent"] else None,
                    "consent_reference": f"CONSENT-2026-{s['student_id']}" if s["consent"] else "",
                },
            )
            # Ensure links are up to date
            if student.user != stu_user or student.class_room != s["class_room"]:
                student.user = stu_user
                student.class_room = s["class_room"]
                student.save()
            seeded_students.append(student)

        self.stdout.write(self.style.SUCCESS(f"✓ Students seeded: {[s.full_name for s in seeded_students]}"))

        # ------------------------------------------------------------------
        # 7. Seed Sessions and Attendance Records
        # ------------------------------------------------------------------
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        # Today's active session for CS-101
        session_today_cs, _ = Session.objects.get_or_create(
            class_room=cs101,
            date=today,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(10, 0),
            defaults={
                "qr_token": secrets.token_urlsafe(32),
                "qr_token_expires_at": timezone.now() + datetime.timedelta(hours=2),
            },
        )

        # Today's session for SE-201
        session_today_se, _ = Session.objects.get_or_create(
            class_room=se201,
            date=today,
            start_time=datetime.time(13, 30),
            end_time=datetime.time(15, 30),
            defaults={
                "qr_token": secrets.token_urlsafe(32),
                "qr_token_expires_at": timezone.now() + datetime.timedelta(hours=4),
            },
        )

        # Yesterday's ended session for CS-101 with historical attendance
        session_prev_cs, _ = Session.objects.get_or_create(
            class_room=cs101,
            date=yesterday,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(10, 0),
            defaults={
                "ended_at": timezone.now() - datetime.timedelta(days=1, hours=2),
            },
        )

        # Create attendance records for yesterday's session
        stu_dara = seeded_students[0]
        stu_bopha = seeded_students[1]
        stu_rithy = seeded_students[3]

        AttendanceRecord.objects.get_or_create(
            student=stu_dara,
            session=session_prev_cs,
            defaults={"status": "present", "method": "qr"},
        )
        AttendanceRecord.objects.get_or_create(
            student=stu_bopha,
            session=session_prev_cs,
            defaults={"status": "late", "method": "face", "confidence_score": 0.88},
        )
        AttendanceRecord.objects.get_or_create(
            student=stu_rithy,
            session=session_prev_cs,
            defaults={"status": "absent", "method": "manual"},
        )

        self.stdout.write(self.style.SUCCESS("✓ Sessions & historical attendance records generated."))

        # ------------------------------------------------------------------
        # 8. Summary Table Output
        # ------------------------------------------------------------------
        self.stdout.write("\n" + "=" * 92)
        self.stdout.write(self.style.SUCCESS("  SMART ATTENDANCE SYSTEM — SEEDED USERS & CREDENTIALS"))
        self.stdout.write("=" * 92)
        self.stdout.write(f"{'Role':<18} | {'Username':<16} | {'Password':<20} | {'Email':<30}")
        self.stdout.write("-" * 92)
        for u in created_users:
            self.stdout.write(f"{u['role']:<18} | {u['username']:<16} | {u['password']:<20} | {u['email']:<30}")
        self.stdout.write("=" * 92)

        self.stdout.write("\n" + self.style.NOTICE("REST API Authentication Tokens:"))
        for u in created_users:
            self.stdout.write(f"  [{u['username']}] => Token: {u['token']}")
        self.stdout.write("=" * 92 + "\n")
