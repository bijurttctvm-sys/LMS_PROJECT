def seo_defaults(request):
    site_root_url = request.build_absolute_uri('/')
    canonical_url = request.build_absolute_uri(request.path)
    default_image_url = request.build_absolute_uri('/static/branding/lms-platform-mark.png')
    default_title = 'Learning Management Platform'
    default_description = (
        'AI-powered multilingual learning management platform for trainees, trainers, '
        'and administrators managing courses, quizzes, and guided learning support.'
    )
    default_keywords = (
        'AI LMS, multilingual learning management system, trainer portal, trainee portal, '
        'quiz generator, learning assistant, course management platform'
    )
    return {
        'seo_site_name': default_title,
        'seo_title': default_title,
        'seo_description': default_description,
        'seo_keywords': default_keywords,
        'seo_robots': 'noindex,nofollow',
        'seo_current_url': canonical_url,
        'seo_canonical_url': canonical_url,
        'seo_site_root_url': site_root_url,
        'seo_default_image_url': default_image_url,
        'seo_og_type': 'website',
        'seo_og_title': default_title,
        'seo_og_description': default_description,
        'seo_og_image_url': default_image_url,
        'seo_twitter_card': 'summary_large_image',
        'seo_locale': 'en_IN',
        'seo_json_ld': '',
    }
