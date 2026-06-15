from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone


def robots_txt(request):
    sitemap_url = request.build_absolute_uri(reverse('sitemap-xml'))
    lines = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin/',
        'Disallow: /chatbot/',
        'Disallow: /courses/',
        'Disallow: /doubt/',
        'Disallow: /quizzes/',
        'Disallow: /student-dashboard/',
        'Disallow: /instructor-dashboard/',
        'Disallow: /admin-dashboard/',
        'Disallow: /users/',
        'Disallow: /videos/',
        f'Sitemap: {sitemap_url}',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


def sitemap_xml(request):
    homepage_url = request.build_absolute_uri(reverse('home'))
    last_modified = timezone.now().date().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{homepage_url}</loc>
    <lastmod>{last_modified}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return HttpResponse(xml, content_type='application/xml')
