from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0002_batch_batchcourse_batchstudent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EnrollmentRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('admin_note', models.CharField(blank=True, max_length=255)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enrollment_requests', to='courses.course')),
                ('reviewed_by', models.ForeignKey(blank=True, limit_choices_to={'role': 'admin'}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_enrollment_requests', to=settings.AUTH_USER_MODEL)),
                ('student', models.ForeignKey(limit_choices_to={'role': 'student'}, on_delete=django.db.models.deletion.CASCADE, related_name='enrollment_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['status', '-requested_at'],
                'unique_together': {('student', 'course')},
            },
        ),
    ]
