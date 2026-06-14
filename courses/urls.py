from django.urls import path

from . import views

urlpatterns = [
    path("",                                     views.course_list,          name="course-list"),
    path("batches/",                             views.batch_list,           name="batch-list"),
    path("batches/create/",                      views.create_batch,         name="create-batch"),
    path("batches/<int:batch_id>/assign-students/", views.assign_students_to_batch, name="assign-students-to-batch"),
    path("batches/<int:batch_id>/assign-courses/",  views.assign_courses_to_batch,  name="assign-courses-to-batch"),
    path("my/",                                  views.my_courses,           name="my-courses"),
    path("create/",                              views.create_course,        name="create-course"),
    path("<int:course_id>/edit/",                views.edit_course,          name="edit-course"),
    path("<int:course_id>/",                     views.course_detail,        name="course-detail"),
    path("<int:course_id>/enroll/",              views.enroll_student,       name="enroll-student"),
    path("<int:course_id>/assign-instructor/",   views.assign_instructor,    name="assign-instructor"),
    path("<int:course_id>/delete-content/",      views.delete_course_content, name="delete-course-content"),
    path("<int:course_id>/delete/",              views.delete_course,        name="delete-course"),
]
