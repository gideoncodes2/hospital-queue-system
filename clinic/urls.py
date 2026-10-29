from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import PhoneAuthenticationForm

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(
        template_name="clinic/login.html", authentication_form=PhoneAuthenticationForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("book/", views.book, name="book"),
    path("appointments/", views.my_appointments, name="my_appointments"),
    path("appointments/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("staff/", views.dashboard, name="dashboard"),
    path("staff/walk-in/", views.walk_in, name="walk_in"),
    path("staff/appointments/<int:pk>/<str:action>/", views.appt_action, name="appt_action"),
]
