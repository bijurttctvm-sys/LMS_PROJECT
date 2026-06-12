from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from users import views as user_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', user_views.home_view, name='home'),
    path('users/', include('users.urls')),
    path('student-dashboard/', user_views.student_dashboard, name='student-dashboard'),
    path('admin-dashboard/', user_views.admin_dashboard, name='admin-dashboard'),
    path('instructor-dashboard/', user_views.instructor_dashboard, name='instructor-dashboard'),
    path('courses/', include('courses.urls')),
    path('videos/', include('videos.urls')),
    path('chatbot/', include('chatbot.urls')),
    path('quizzes/', include('quizzes.urls')),
    path('doubt/', include('doubt_sessions.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
