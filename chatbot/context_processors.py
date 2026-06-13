def learning_assistant_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or user.role != 'student':
        return {}

    enrollments = (
        user.enrollments
        .filter(is_active=True)
        .select_related('course')
    )
    chatbot_courses = [
        {'id': enrollment.course_id, 'title': enrollment.course.title}
        for enrollment in enrollments
    ]

    return {
        'chatbot_course_id': chatbot_courses[0]['id'] if chatbot_courses else 0,
        'chatbot_courses': chatbot_courses,
        'chatbot_enrolled': bool(chatbot_courses),
    }
