from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("videos", "0003_video_processing_started_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="video",
            name="status",
            field=models.CharField(
                choices=[
                    ("UPLOADED", "Awaiting Study Material"),
                    ("PROCESSING", "Processing"),
                    ("READY", "Ready"),
                    ("FAILED", "Failed"),
                ],
                default="UPLOADED",
                max_length=20,
            ),
        ),
    ]
