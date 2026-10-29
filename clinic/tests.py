from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Appointment, Department, Doctor, Notification, Patient
from .utils import normalize_phone

Status = Appointment.Status


def make_patient(n):
    return Patient.objects.create(full_name=f"Patient {n}", phone=f"+25470000{n:04d}")


class PhoneTests(TestCase):
    def test_formats_are_normalised(self):
        for raw in ["0712345678", "712345678", "+254712345678", "254 712 345 678", "0712-345-678"]:
            self.assertEqual(normalize_phone(raw), "+254712345678")

    def test_invalid_numbers_rejected(self):
        for raw in ["", "12345", "+1555123456", "abc"]:
            with self.assertRaises(ValueError):
                normalize_phone(raw)


class BookingTests(TestCase):
    def setUp(self):
        self.doctor = Doctor.objects.create(name="Test", department=Department.objects.create(name="General"))
        self.day = timezone.localdate() + timedelta(days=1)
        self.slot = self.doctor.start_time.replace(hour=9, minute=0)

    def test_slot_cannot_be_double_booked(self):
        services.book_appointment(make_patient(1), self.doctor, self.day, self.slot)
        with self.assertRaises(services.BookingError):
            services.book_appointment(make_patient(2), self.doctor, self.day, self.slot)

    def test_cancelled_slot_can_be_rebooked(self):
        appt = services.book_appointment(make_patient(1), self.doctor, self.day, self.slot)
        services.cancel_appointment(appt)
        again = services.book_appointment(make_patient(2), self.doctor, self.day, self.slot)
        self.assertNotEqual(appt.queue_number, again.queue_number)  # numbers are never reused

    def test_booked_slot_disappears_from_available(self):
        self.assertIn(self.slot, services.available_slots(self.doctor, self.day))
        services.book_appointment(make_patient(1), self.doctor, self.day, self.slot)
        self.assertNotIn(self.slot, services.available_slots(self.doctor, self.day))

    def test_one_active_booking_per_doctor_per_day(self):
        patient = make_patient(1)
        services.book_appointment(patient, self.doctor, self.day, self.slot)
        with self.assertRaises(services.BookingError):
            services.book_appointment(patient, self.doctor, self.day, self.slot.replace(hour=10))

    def test_queue_numbers_are_sequential_and_confirmation_sent(self):
        a = services.book_appointment(make_patient(1), self.doctor, self.day, self.slot)
        b = services.book_appointment(make_patient(2), self.doctor, self.day, self.slot.replace(hour=10))
        self.assertEqual((a.queue_number, b.queue_number), (1, 2))
        self.assertEqual(Notification.objects.filter(kind="confirmation").count(), 2)

    def test_past_days_have_no_slots(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self.assertEqual(services.available_slots(self.doctor, yesterday), [])


class QueueTests(TestCase):
    """Five patients, 15 minutes apart from 09:00 today, default average 15 min."""

    def setUp(self):
        self.doctor = Doctor.objects.create(
            name="Test", department=Department.objects.create(name="General"), default_consult_minutes=15
        )
        self.base = timezone.localtime().replace(hour=9, minute=0, second=0, microsecond=0)
        self.appts = []
        for i in range(5):
            sort_time = self.base + timedelta(minutes=15 * i)
            self.appts.append(Appointment.objects.create(
                patient=make_patient(i), doctor=self.doctor, date=self.base.date(),
                slot_time=sort_time.time(), sort_time=sort_time, queue_number=i + 1, status=Status.CHECKED_IN,
            ))

    def test_wait_is_patients_ahead_times_average(self):
        st = services.queue_status(self.appts[2], now=self.base)
        self.assertEqual((st.ahead, st.wait_minutes), (2, 30))

    def test_running_consultation_counts_only_its_remaining_time(self):
        first = self.appts[0]
        first.status, first.consult_start = Status.IN_CONSULTATION, self.base
        first.save()
        now = self.base + timedelta(minutes=5)
        self.assertEqual(services.queue_status(self.appts[1], now=now).wait_minutes, 10)
        self.assertEqual(services.queue_status(self.appts[2], now=now).wait_minutes, 25)

    def test_never_told_a_time_before_their_slot(self):
        early = self.base - timedelta(hours=2)
        self.assertEqual(services.queue_status(self.appts[0], now=early).wait_minutes, 120)

    def test_average_learns_from_real_consultations(self):
        self.assertEqual(services.avg_consult_minutes(self.doctor), 15.0)  # default until 3 samples
        for i, minutes in enumerate([6, 8, 10]):
            appt = self.appts[i]
            appt.status = Status.COMPLETED
            appt.consult_start = self.base + timedelta(minutes=20 * i)
            appt.consult_end = appt.consult_start + timedelta(minutes=minutes)
            appt.save()
        self.assertAlmostEqual(services.avg_consult_minutes(self.doctor), 8.0)
        self.assertEqual(services.queue_status(self.appts[4], now=self.base + timedelta(hours=1)).wait_minutes, 8)

    def test_walk_in_joins_at_the_back_of_the_current_time(self):
        walk = services.add_walk_in(make_patient(99), self.doctor)
        self.assertTrue(walk.is_walk_in)
        self.assertEqual(walk.status, Status.CHECKED_IN)

    def test_cannot_start_two_consultations_at_once(self):
        services.start_consultation(self.appts[0])
        with self.assertRaises(services.BookingError):
            services.start_consultation(self.appts[1])

    def test_finishing_records_duration(self):
        services.start_consultation(self.appts[0])
        services.finish_consultation(self.appts[0])
        self.appts[0].refresh_from_db()
        self.assertEqual(self.appts[0].status, Status.COMPLETED)
        self.assertIsNotNone(self.appts[0].consult_end)


class AlertTests(QueueTests):
    def test_near_alert_sent_once_to_patients_within_threshold(self):
        # Threshold is 30 min: patients 0, 1 and 2 qualify (0, 15 and 30 min), patients 3 and 4 do not.
        self.assertEqual(services.process_alerts(now=self.base), 3)
        self.assertEqual(Notification.objects.filter(kind="near").count(), 3)
        self.assertEqual(services.process_alerts(now=self.base), 0)  # no repeats

    def test_delay_update_when_doctor_runs_late(self):
        services.process_alerts(now=self.base)
        first = self.appts[0]
        first.status, first.consult_start = Status.IN_CONSULTATION, self.base
        first.save()
        # 40 minutes in, the first consultation is far longer than average.
        sent = services.process_alerts(now=self.base + timedelta(minutes=40))
        self.assertGreaterEqual(sent, 1)
        delay = Notification.objects.filter(kind="delay", appointment=self.appts[2])
        self.assertEqual(delay.count(), 1)
        self.assertIn("running late", delay.first().message)

    def test_small_changes_do_not_spam(self):
        services.process_alerts(now=self.base)
        self.assertEqual(services.process_alerts(now=self.base + timedelta(minutes=2)), 0)

    @override_settings(SMS_BACKEND="console")
    def test_finishing_a_consultation_triggers_alert_check(self):
        services.start_consultation(self.appts[0])
        services.finish_consultation(self.appts[0])
        self.assertTrue(Notification.objects.filter(kind="near").exists())

    def test_reminders_sent_once_for_tomorrow(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        services.book_appointment(make_patient(50), self.doctor, tomorrow, self.doctor.start_time)
        self.assertEqual(services.send_reminders(), 1)
        self.assertEqual(services.send_reminders(), 0)


class ViewTests(TestCase):
    def setUp(self):
        self.doctor = Doctor.objects.create(name="Test", department=Department.objects.create(name="General"))

    def register(self):
        return self.client.post(reverse("register"), {
            "full_name": "Amina Yusuf", "phone": "0712345678", "password1": "secret123", "password2": "secret123",
        })

    def test_register_login_and_book(self):
        self.assertRedirects(self.register(), reverse("book"))
        self.client.logout()
        self.assertTrue(self.client.login(username="+254712345678", password="secret123"))
        day = timezone.localdate() + timedelta(days=1)
        response = self.client.post(reverse("book"), {"doctor": self.doctor.pk, "date": day.isoformat(), "slot": "09:00"})
        self.assertRedirects(response, reverse("my_appointments"))
        self.assertContains(self.client.get(reverse("my_appointments")), "Q001")

    def test_login_form_accepts_local_phone_format(self):
        self.register()
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": "0712 345 678", "password": "secret123"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_duplicate_phone_rejected(self):
        self.register()
        self.client.logout()
        response = self.register()
        self.assertContains(response, "already exists")

    def test_patients_cannot_open_staff_dashboard(self):
        self.register()
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_staff_runs_a_consultation_from_the_dashboard(self):
        staff = User.objects.create_user("nurse", password="pw12345", is_staff=True)
        self.client.login(username="nurse", password="pw12345")
        self.assertContains(self.client.post(reverse("walk_in"), {
            "doctor": self.doctor.pk, "full_name": "Walk In", "phone": "0700111222"}, follow=True), "Q001")
        appt = Appointment.objects.get()
        self.client.post(reverse("appt_action", args=[appt.pk, "start"]))
        self.client.post(reverse("appt_action", args=[appt.pk, "finish"]))
        appt.refresh_from_db()
        self.assertEqual(appt.status, Status.COMPLETED)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_doctor_login_only_sees_own_queue(self):
        other = Doctor.objects.create(name="Other", department=self.doctor.department)
        staff = User.objects.create_user("dr", password="pw12345", is_staff=True)
        self.doctor.user = staff
        self.doctor.save()
        appt = services.add_walk_in(make_patient(1), other)
        self.client.login(username="dr", password="pw12345")
        self.assertEqual(self.client.post(reverse("appt_action", args=[appt.pk, "start"])).status_code, 403)
