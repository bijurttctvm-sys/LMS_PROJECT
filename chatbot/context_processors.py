def learning_assistant_context(request):
    from django.conf import settings
    from django.core.cache import cache

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or user.role != 'student':
        return {}

    enrollments = (
        user.enrollments
        .filter(is_active=True)
        .select_related('course')
        .only('course_id', 'course__title')
    )
    chatbot_courses = [
        {'id': enrollment.course_id, 'title': enrollment.course.title}
        for enrollment in enrollments
    ]

    if (
        chatbot_courses
        and getattr(settings, 'CHATBOT_WARMUP_ENABLED', False)
        and cache.add('chatbot:warmup:started', True, timeout=300)
    ):
        try:
            from utils.embeddings import warm_up_embeddings_async
            from utils.pinecone_client import warm_up_index_async

            warm_up_embeddings_async()
            warm_up_index_async()
        except Exception:
            # Warmup is opportunistic only; the assistant should remain usable.
            pass

    return {
        'chatbot_course_id': chatbot_courses[0]['id'] if chatbot_courses else 0,
        'chatbot_courses': chatbot_courses,
        'chatbot_enrolled': bool(chatbot_courses),
    }
