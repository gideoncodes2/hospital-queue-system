from django.contrib import admin

from .models import Appointment, Department, Doctor, Notification, Patient


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ["name", "department", "start_time", "end_time", "slot_minutes", "default_consult_minutes", "is_active"]
    list_filter = ["department", "is_active"]


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["full_name", "phone"]
    search_fields = ["full_name", "phone"]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["ticket", "patient", "doctor", "date", "slot_time", "status", "is_walk_in"]
    list_filter = ["date", "status", "doctor", "is_walk_in"]
    date_hierarchy = "date"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["appointment", "kind", "delivered", "created_at"]
    list_filter = ["kind", "delivered"]
