from django.urls import path

from . import views

urlpatterns = [
    path("register/",    views.register_view,   name="register"),
    path("login/",       views.login_view,       name="login"),
    path("logout/",      views.logout_view,      name="logout"),
    path("profile/",     views.profile_view,     name="profile"),
    path("change-password/", views.change_password_view, name="change-password"),
    path("create-user/", views.create_user_view, name="create-user"),
]
