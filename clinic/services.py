"""Business logic: booking, the live queue, wait-time estimates and SMS alerts."""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from . import sms
from .models import Appointment, Notification

Status = Appointment.Status
Kind = Notification.Kind


class BookingError(Exception):
    """Raised with a message that is safe to show to the user."""


# --------------------------------------------------------------------------
# Slots and booking
# --------------------------------------------------------------------------
def available_slots(doctor, day):
    """Free appointment times for a doctor on a given day."""
    now = timezone.localtime()
    if day < now.date():
        return []
    taken = set(
        Appointment.objects.filter(doctor=doctor, date=day)
        .exclude(status=Status.CANCELLED)
        .exclude(slot_time__isnull=True)
        .values_list("slot_time", flat=True)
    )
    slots = []
    current = datetime.combine(day, doctor.start_time)
    end = datetime.combine(day, doctor.end_time)
    step = timedelta(minutes=doctor.slot_minutes)
    while current < end:
        t = current.time()
        in_past = day == now.date() and t <= now.time()
        if t not in taken and not in_past:
            slots.append(t)
        current += step
    return slots


def next_queue_number(doctor, day):
    # Cancelled tickets are included so a number is never handed out twice.
    last = Appointment.objects.filter(doctor=doctor, date=day).aggregate(m=Max("queue_number"))["m"]
    return (last or 0) + 1


def book_appointment(patient, doctor, day, slot_time):
    if slot_time not in available_slots(doctor, day):
        raise BookingError("That time is no longer available. Please choose another.")
    if Appointment.objects.filter(
        patient=patient, doctor=doctor, date=day, status__in=Appointment.ACTIVE
    ).exists():
        raise BookingError("You already have an appointment with this doctor on that day.")

    sort_time = timezone.make_aware(datetime.combine(day, slot_time))
    for _ in range(3):
        try:
            with transaction.atomic():
                appt = Appointment.objects.create(
                    patient=patient, doctor=doctor, date=day, slot_time=slot_time,
                    sort_time=sort_time, queue_number=next_queue_number(doctor, day),
                )
            break
        except IntegrityError:
            # Someone else took the slot or the number at the same moment.
            if slot_time not in available_slots(doctor, day):
                raise BookingError("Someone just booked that time. Please choose another.")
    else:
        raise BookingError("Could not complete the booking. Please try again.")

    when = f"{day:%a %d %b} at {slot_time:%H:%M}"
    notify(
        appt, Kind.CONFIRMATION,
        f"Booked: Dr {doctor.name}, {doctor.department}, {when}. "
        f"Your queue number is {appt.ticket}. We will text you when your turn is near.",
    )
    return appt


def add_walk_in(patient, doctor):
    """Register a patient who arrived without an appointment. They join the queue now."""
    day = timezone.localdate()
    now = timezone.now()
    if Appointment.objects.filter(
        patient=patient, doctor=doctor, date=day, status__in=Appointment.ACTIVE
    ).exists():
        raise BookingError("This patient is already in the queue for this doctor today.")
    for _ in range(3):
        try:
            with transaction.atomic():
                appt = Appointment.objects.create(
                    patient=patient, doctor=doctor, date=day, slot_time=None, sort_time=now,
                    queue_number=next_queue_number(doctor, day), status=Status.CHECKED_IN,
                    is_walk_in=True, checked_in_at=now,
                )
            break
        except IntegrityError:
            continue
    else:
        raise BookingError("Could not add the patient. Please try again.")
    status = queue_status(appt)
    notify(
        appt, Kind.CONFIRMATION,
        f"You are in the queue for Dr {doctor.name}. Queue number {appt.ticket}. "
        f"Estimated wait: about {status.wait_minutes} min.",
    )
    return appt


# --------------------------------------------------------------------------
# Consultation lifecycle (used by the staff dashboard)
# --------------------------------------------------------------------------
def check_in(appt):
    if appt.status != Status.BOOKED:
        raise BookingError("Only booked patients can be checked in.")
    appt.status = Status.CHECKED_IN
    appt.checked_in_at = timezone.now()
    appt.save(update_fields=["status", "checked_in_at"])


def start_consultation(appt):
    if appt.status not in Appointment.WAITING:
        raise BookingError("This patient is not waiting.")
    busy = Appointment.objects.filter(
        doctor=appt.doctor, date=appt.date, status=Status.IN_CONSULTATION
    ).exists()
    if busy:
        raise BookingError("Finish the current consultation before starting the next one.")
    appt.status = Status.IN_CONSULTATION
    appt.consult_start = timezone.now()
    appt.save(update_fields=["status", "consult_start"])


def finish_consultation(appt):
    if appt.status != Status.IN_CONSULTATION:
        raise BookingError("This patient is not with the doctor.")
    appt.status = Status.COMPLETED
    appt.consult_end = timezone.now()
    appt.save(update_fields=["status", "consult_end"])
    # The average has just changed, so recheck everyone still waiting.
    process_alerts(doctor=appt.doctor)


def mark_no_show(appt):
    if appt.status not in Appointment.WAITING:
        raise BookingError("Only waiting patients can be marked as no-show.")
    appt.status = Status.NO_SHOW
    appt.save(update_fields=["status"])


def cancel_appointment(appt):
    if appt.status not in Appointment.WAITING:
        raise BookingError("This appointment can no longer be cancelled.")
    appt.status = Status.CANCELLED
    appt.save(update_fields=["status"])


# --------------------------------------------------------------------------
# Consultation time and wait estimates
# --------------------------------------------------------------------------
def avg_consult_minutes(doctor, sample=20, min_samples=3):
    """Average length of the doctor's most recent timed consultations.

    Falls back to the doctor's default until enough real data exists.
    """
    recent = Appointment.objects.filter(
        doctor=doctor, status=Status.COMPLETED,
        consult_start__isnull=False, consult_end__isnull=False,
    ).order_by("-consult_end")[:sample]
    durations = [a.consult_minutes for a in recent if a.consult_minutes >= 1]
    if len(durations) < min_samples:
        return float(doctor.default_consult_minutes)
    return sum(durations) / len(durations)


@dataclass
class QueueStatus:
    ahead: int            # patients in front of this one
    wait_minutes: int     # minutes until this patient is expected to be seen
    expected_start: datetime
    avg_minutes: float    # the consultation average the estimate is based on


def queue_status(appt, now=None):
    """Where a patient is in the queue and roughly when they will be seen.

    wait = (waiting patients ahead x average consultation length)
           + time left in the consultation happening now.
    A booked patient is never told a time earlier than their slot.
    """
    now = now or timezone.now()
    avg = avg_consult_minutes(appt.doctor)

    if appt.status == Status.IN_CONSULTATION:
        return QueueStatus(0, 0, now, avg)

    active = Appointment.objects.filter(doctor=appt.doctor, date=appt.date, status__in=Appointment.ACTIVE)
    ahead_qs = active.filter(
        Q(sort_time__lt=appt.sort_time) | Q(sort_time=appt.sort_time, id__lt=appt.id)
    )
    waiting_ahead = ahead_qs.exclude(status=Status.IN_CONSULTATION).count()

    remaining = 0.0
    current = active.filter(status=Status.IN_CONSULTATION).first()
    if current and current.consult_start:
        elapsed = (now - current.consult_start).total_seconds() / 60
        remaining = max(avg - elapsed, 1.0)   # a running consultation never counts as "already over"

    free_at = now + timedelta(minutes=waiting_ahead * avg + remaining)
    expected = max(free_at, appt.sort_time)
    wait = max(math.ceil((expected - now).total_seconds() / 60), 0)
    return QueueStatus(ahead_qs.count(), wait, expected, avg)


# --------------------------------------------------------------------------
# SMS alerts
# --------------------------------------------------------------------------
def notify(appt, kind, message):
    delivered = sms.send_sms(appt.patient.phone, message)
    return Notification.objects.create(appointment=appt, kind=kind, message=message, delivered=delivered)


def process_alerts(doctor=None, now=None):
    """Send "turn is near" and "delayed" SMS messages for today's waiting patients.

    Called whenever a consultation finishes, and every minute by the
    process_alerts management command (so alerts also fire as time passes).
    Returns the number of messages sent.
    """
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    waiting = Appointment.objects.filter(date=today, status__in=Appointment.WAITING).select_related(
        "patient", "doctor__department"
    )
    if doctor:
        waiting = waiting.filter(doctor=doctor)

    sent = 0
    for appt in waiting:
        st = queue_status(appt, now)
        expected_local = timezone.localtime(st.expected_start).strftime("%H:%M")
        if st.wait_minutes > settings.ALERT_MINUTES_BEFORE:
            continue
        if appt.notified_expected_start is None:
            notify(
                appt, Kind.NEAR,
                f"Your turn with Dr {appt.doctor.name} is near: about {st.wait_minutes} min "
                f"(around {expected_local}), {st.ahead} ahead of you. Queue number {appt.ticket}. "
                f"Please be at the {appt.doctor.department} waiting area.",
            )
        elif st.expected_start - appt.notified_expected_start >= timedelta(minutes=settings.DELAY_UPDATE_MINUTES):
            notify(
                appt, Kind.DELAY,
                f"Update: Dr {appt.doctor.name} is running late. Your new expected time is around "
                f"{expected_local} (about {st.wait_minutes} min). Queue number {appt.ticket}. Sorry for the wait.",
            )
        else:
            continue
        appt.notified_expected_start = st.expected_start
        appt.save(update_fields=["notified_expected_start"])
        sent += 1
    return sent


def send_reminders(day=None):
    """Day-before reminder for booked appointments. Run once a day, e.g. at 18:00."""
    day = day or timezone.localdate() + timedelta(days=1)
    appts = Appointment.objects.filter(date=day, status=Status.BOOKED).select_related("patient", "doctor")
    sent = 0
    for appt in appts:
        if appt.notifications.filter(kind=Kind.REMINDER).exists():
            continue
        notify(
            appt, Kind.REMINDER,
            f"Reminder: you have an appointment with Dr {appt.doctor.name} tomorrow at "
            f"{appt.slot_time:%H:%M}. Queue number {appt.ticket}.",
        )
        sent += 1
    return sent
