from django.urls import path

from . import views

urlpatterns = [
    path("register/",    views.register_view,   name="register"),
    path("login/",       views.login_view,       name="login"),
    path("logout/",      views.logout_view,      name="logout"),
    path("profile/",     views.profile_view,     name="profile"),
    path("change-password/", views.change_password_view, name="change-password"),
    path("create-user/", views.create_user_view, name="create-user"),
    path("manage/<str:role>/", views.manage_users_view, name="manage-users"),
    path("manage/<int:user_id>/toggle-active/", views.toggle_user_active_view, name="toggle-user-active"),
    path("manage/<int:user_id>/delete/", views.delete_user_view, name="delete-user"),
]
