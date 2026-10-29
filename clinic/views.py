from datetime import date as date_cls
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import RegisterForm, WalkInForm
from .models import Appointment, Doctor, Patient

Status = Appointment.Status
staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="login")


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard" if request.user.is_staff else "my_appointments")
    return render(request, "clinic/home.html")


def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account is ready. You can book an appointment now.")
        return redirect("book")
    return render(request, "clinic/register.html", {"form": form})


# --------------------------------------------------------------------------
# Patient pages
# --------------------------------------------------------------------------
@login_required
def book(request):
    patient = getattr(request.user, "patient", None)
    if patient is None:
        messages.error(request, "Only patient accounts can book appointments.")
        return redirect("home")
    doctors = Doctor.objects.filter(is_active=True).select_related("department")

    if request.method == "POST":
        doctor_id, day_raw = request.POST.get("doctor", ""), request.POST.get("date", "")
        try:
            doctor = get_object_or_404(doctors, pk=doctor_id)
            day = date_cls.fromisoformat(day_raw)
            slot = datetime.strptime(request.POST.get("slot", ""), "%H:%M").time()
            services.book_appointment(patient, doctor, day, slot)
        except (ValueError, Http404):
            messages.error(request, "Choose a doctor, a date and a time.")
            return redirect("book")
        except services.BookingError as exc:
            messages.error(request, str(exc))
            return redirect(f"{reverse('book')}?doctor={doctor.pk}&date={day}")
        messages.success(request, "Appointment booked. We have sent you a confirmation text.")
        return redirect("my_appointments")

    today = timezone.localdate()
    doctor = None
    try:
        doctor = doctors.filter(pk=request.GET.get("doctor")).first() if request.GET.get("doctor") else None
    except ValueError:
        pass
    try:
        day = date_cls.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = today
    slots = services.available_slots(doctor, day) if doctor else []
    return render(request, "clinic/book.html", {
        "doctors": doctors, "doctor": doctor, "day": day, "today": today, "slots": slots,
    })


@login_required
def my_appointments(request):
    patient = getattr(request.user, "patient", None)
    if patient is None:
        return redirect("dashboard" if request.user.is_staff else "home")
    today = timezone.localdate()
    upcoming = list(
        patient.appointments.filter(date__gte=today, status__in=Appointment.ACTIVE)
        .select_related("doctor__department").order_by("date", "sort_time")
    )
    for appt in upcoming:
        appt.live = services.queue_status(appt) if appt.date == today else None
    history = (
        patient.appointments.exclude(id__in=[a.id for a in upcoming])
        .select_related("doctor__department").order_by("-date", "-sort_time")[:8]
    )
    return render(request, "clinic/my_appointments.html", {"upcoming": upcoming, "history": history})


@login_required
@require_POST
def cancel(request, pk):
    appt = get_object_or_404(Appointment, pk=pk, patient__user=request.user)
    try:
        services.cancel_appointment(appt)
        messages.success(request, "Appointment cancelled.")
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("my_appointments")


# --------------------------------------------------------------------------
# Staff pages
# --------------------------------------------------------------------------
def _staff_doctors(user):
    doctors = Doctor.objects.filter(is_active=True).select_related("department")
    own = getattr(user, "doctor_profile", None)
    return (doctors.filter(pk=own.pk) if own else doctors), own


def _dashboard_url(doctor_id):
    return f"{reverse('dashboard')}?doctor={doctor_id}"


@staff_required
def dashboard(request):
    doctors, _ = _staff_doctors(request.user)
    try:
        raw = request.GET.get("doctor")
        doctor = doctors.filter(pk=raw).first() if raw else doctors.first()
    except ValueError:
        doctor = doctors.first()
    context = {"doctors": doctors, "doctor": doctor, "walk_in_form": WalkInForm()}
    if doctor:
        now = timezone.now()
        appts = list(
            Appointment.objects.filter(doctor=doctor, date=timezone.localdate())
            .select_related("patient").order_by("sort_time", "id")
        )
        current = next((a for a in appts if a.status == Status.IN_CONSULTATION), None)
        waiting = [a for a in appts if a.status in Appointment.WAITING]
        for appt in waiting:
            appt.live = services.queue_status(appt, now)
        context.update({
            "current": current,
            "current_minutes": int((now - current.consult_start).total_seconds() // 60) if current else 0,
            "waiting": waiting,
            "finished": [a for a in appts if a.status in (Status.COMPLETED, Status.NO_SHOW, Status.CANCELLED)],
            "avg_minutes": round(services.avg_consult_minutes(doctor)),
        })
    return render(request, "clinic/dashboard.html", context)


ACTIONS = {
    "check_in": services.check_in,
    "start": services.start_consultation,
    "finish": services.finish_consultation,
    "no_show": services.mark_no_show,
}


@staff_required
@require_POST
def appt_action(request, pk, action):
    appt = get_object_or_404(Appointment, pk=pk)
    _, own = _staff_doctors(request.user)
    if own and appt.doctor_id != own.pk:
        raise PermissionDenied
    handler = ACTIONS.get(action)
    if handler is None:
        raise Http404
    try:
        handler(appt)
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect(_dashboard_url(appt.doctor_id))


@staff_required
@require_POST
def walk_in(request):
    doctors, _ = _staff_doctors(request.user)
    doctor = get_object_or_404(doctors, pk=request.POST.get("doctor"))
    form = WalkInForm(request.POST)
    if form.is_valid():
        patient, _ = Patient.objects.get_or_create(
            phone=form.cleaned_data["phone"], defaults={"full_name": form.cleaned_data["full_name"]}
        )
        try:
            appt = services.add_walk_in(patient, doctor)
            wait = services.queue_status(appt).wait_minutes
            messages.success(request, f"{appt.ticket} issued to {patient.full_name}. Estimated wait: about {wait} min.")
        except services.BookingError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(_dashboard_url(doctor.pk))
