from django.urls import path

from . import views

urlpatterns = [
    path("",                                     views.course_list,          name="course-list"),
    path("my/",                                  views.my_courses,           name="my-courses"),
    path("create/",                              views.create_course,        name="create-course"),
    path("<int:course_id>/",                     views.course_detail,        name="course-detail"),
    path("<int:course_id>/enroll/",              views.enroll_student,       name="enroll-student"),
    path("<int:course_id>/assign-instructor/",   views.assign_instructor,    name="assign-instructor"),
    path("<int:course_id>/delete-content/",      views.delete_course_content, name="delete-course-content"),
]
