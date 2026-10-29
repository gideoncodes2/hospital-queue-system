from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from clinic import services
from clinic.models import Appointment, Department, Doctor, Patient

Status = Appointment.Status


class Command(BaseCommand):
    help = "Create demo departments, doctors, patients and a live queue for today."

    def handle(self, *args, **options):
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", password="admin1234")

        general, _ = Department.objects.get_or_create(name="General Outpatient")
        Department.objects.get_or_create(name="Paediatrics")
        Department.objects.get_or_create(name="Dental")

        staff, created = User.objects.get_or_create(username="dr.achieng", defaults={"is_staff": True})
        if created:
            staff.set_password("demo1234")
            staff.save()
        doctor, _ = Doctor.objects.get_or_create(
            name="Achieng Otieno", department=general, defaults={"user": staff, "default_consult_minutes": 12}
        )
        for name, dept in [("Wanjiku Kamau", "Paediatrics"), ("Brian Mutua", "Dental")]:
            Doctor.objects.get_or_create(name=name, department=Department.objects.get(name=dept))

        today = timezone.localdate()
        if Appointment.objects.filter(doctor=doctor, date=today).exists():
            self.stdout.write("Today's demo queue already exists.")
            return

        people = [("Mary Wambui", "+254700000001"), ("John Kiprop", "+254700000002"),
                  ("Faith Akinyi", "+254700000003"), ("Peter Mwangi", "+254700000004"),
                  ("Grace Naliaka", "+254700000005"), ("Samuel Oduor", "+254700000006"),
                  ("Lucy Chebet", "+254700000007")]
        patients = []
        for name, phone in people:
            user, made = User.objects.get_or_create(username=phone)
            if made:
                user.set_password("demo1234")
                user.save()
            patient, _ = Patient.objects.get_or_create(phone=phone, defaults={"full_name": name, "user": user})
            patients.append(patient)

        now = timezone.now()
        # Three finished consultations earlier today give the system a real average to learn from.
        for i, minutes in enumerate([11, 14, 13]):
            start = now - timedelta(hours=2, minutes=40 - i * 15)
            Appointment.objects.create(
                patient=patients[i], doctor=doctor, date=today, sort_time=start,
                slot_time=timezone.localtime(start).time(), queue_number=services.next_queue_number(doctor, today),
                status=Status.COMPLETED, consult_start=start, consult_end=start + timedelta(minutes=minutes),
            )
        # One patient with the doctor now, and three waiting.
        current_start = now - timedelta(minutes=5)
        Appointment.objects.create(
            patient=patients[3], doctor=doctor, date=today, sort_time=current_start,
            slot_time=timezone.localtime(current_start).time(), queue_number=services.next_queue_number(doctor, today),
            status=Status.IN_CONSULTATION, consult_start=current_start,
        )
        for i, patient in enumerate(patients[4:]):
            slot = now + timedelta(minutes=10 + i * 15)
            Appointment.objects.create(
                patient=patient, doctor=doctor, date=today, sort_time=slot,
                slot_time=timezone.localtime(slot).time(), queue_number=services.next_queue_number(doctor, today),
                status=Status.CHECKED_IN, checked_in_at=now,
            )
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready.\n  Staff:   dr.achieng / demo1234\n  Admin:   admin / admin1234\n"
            "  Patient: 0700000005 / demo1234 (waiting in today's queue)"
        ))
