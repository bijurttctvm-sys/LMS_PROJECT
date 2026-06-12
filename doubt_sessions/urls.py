from django.urls import path

from . import views

urlpatterns = [
    # Student
    path("request/",                             views.request_session,   name="request-session"),
    path("sessions/<int:session_id>/choose/",    views.choose_slot,       name="choose-slot"),
    path("sessions/",                            views.my_sessions,       name="my-sessions"),

    # Instructor
    path("instructor/sessions/",                 views.instructor_sessions, name="instructor-sessions"),
    path("sessions/<int:session_id>/propose/",   views.propose_slots,     name="propose-slots"),
    path("sessions/<int:session_id>/outcome/",   views.mark_outcome,      name="mark-outcome"),

    # Admin
    path("admin/sessions/",                      views.admin_sessions,      name="admin-sessions"),
    path("admin/sessions/<int:session_id>/close/", views.admin_close_session, name="admin-close-session"),
]
