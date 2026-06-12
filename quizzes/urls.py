from django.urls import path

from . import views

urlpatterns = [
    # Admin
    path('drafts/',                       views.quiz_draft_list,   name='quiz-draft-list'),
    path('drafts/<int:draft_id>/review/', views.review_quiz_draft, name='review-quiz-draft'),
    path('publish/<int:video_id>/',       views.publish_quiz,      name='publish-quiz'),
    # Student
    path('',                              views.student_quiz_list, name='student-quiz-list'),
    path('<int:quiz_id>/take/',           views.take_quiz,         name='take-quiz'),
    path('<int:quiz_id>/results/',        views.quiz_results,      name='quiz-results'),
]
