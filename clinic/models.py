import datetime

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=100)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="doctors")
    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="doctor_profile",
        help_text="Staff login that runs this doctor's queue. The user must have 'staff status'.",
    )
    start_time = models.TimeField(default=datetime.time(8, 0))
    end_time = models.TimeField(default=datetime.time(16, 0))
    slot_minutes = models.PositiveSmallIntegerField(default=15)
    default_consult_minutes = models.PositiveSmallIntegerField(
        default=15,
        help_text="Used for wait estimates until enough real consultations have been timed.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "name"]

    def __str__(self):
        return f"Dr {self.name} ({self.department})"


class Patient(models.Model):
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.CASCADE, related_name="patient")
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=16, unique=True)

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class Appointment(models.Model):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CHECKED_IN = "checked_in", "Waiting"
        IN_CONSULTATION = "in_consultation", "With doctor"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"

    ACTIVE = [Status.BOOKED, Status.CHECKED_IN, Status.IN_CONSULTATION]
    WAITING = [Status.BOOKED, Status.CHECKED_IN]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="appointments")
    date = models.DateField()
    slot_time = models.TimeField(null=True, blank=True, help_text="Empty for walk-ins.")
    # Position in the queue: the slot time for booked patients, the arrival time for walk-ins.
    sort_time = models.DateTimeField()
    queue_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED)
    is_walk_in = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    consult_start = models.DateTimeField(null=True, blank=True)
    consult_end = models.DateTimeField(null=True, blank=True)
    # The expected start time the patient was last told about by SMS.
    notified_expected_start = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["date", "sort_time", "id"]
        constraints = [
            # One live appointment per doctor per slot. Cancelled slots can be rebooked.
            models.UniqueConstraint(
                fields=["doctor", "date", "slot_time"],
                condition=~Q(status="cancelled"),
                name="unique_active_slot",
            ),
            models.UniqueConstraint(fields=["doctor", "date", "queue_number"], name="unique_queue_number"),
        ]

    def __str__(self):
        return f"{self.ticket} {self.patient.full_name} with {self.doctor}"

    @property
    def ticket(self):
        return f"Q{self.queue_number:03d}"

    @property
    def consult_minutes(self):
        if self.consult_start and self.consult_end:
            return (self.consult_end - self.consult_start).total_seconds() / 60
        return None


class Notification(models.Model):
    class Kind(models.TextChoices):
        CONFIRMATION = "confirmation", "Booking confirmation"
        REMINDER = "reminder", "Day-before reminder"
        NEAR = "near", "Turn is near"
        DELAY = "delay", "Delay update"

    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.TextField()
    delivered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.appointment.patient.phone}"
