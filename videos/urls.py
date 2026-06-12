from django.urls import path

from . import views

urlpatterns = [
    path('upload/', views.upload_video_view, name='upload-video'),
    path('<int:video_id>/material/', views.upload_material_view, name='upload-material'),
    path('course/<int:course_id>/', views.video_list_view, name='video-list'),
    path('<int:video_id>/', views.video_detail_view, name='video-detail'),
    path('<int:video_id>/status/',        views.video_status_api,    name='video-status'),
    path('<int:video_id>/generate-quiz/', views.generate_quiz_view,  name='generate-quiz'),
]
