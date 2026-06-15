from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0003_enrollmentrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='enrollmentrequest',
            name='request_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='enrollmentrequest',
            name='admin_note',
            field=models.TextField(blank=True),
        ),
    ]
