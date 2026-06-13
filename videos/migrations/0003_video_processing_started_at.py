from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0002_transcriptchunk_topic_segment_video_english_pdf_key_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='video',
            name='processing_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
