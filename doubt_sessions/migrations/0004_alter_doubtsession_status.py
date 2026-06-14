from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('doubt_sessions', '0003_doubtsession_instructor_postponed_once_and_statuses'),
    ]

    operations = [
        migrations.AlterField(
            model_name='doubtsession',
            name='status',
            field=models.CharField(
                choices=[
                    ('requested', 'Requested'),
                    ('selected', 'Slots Proposed'),
                    ('confirmed', 'Confirmed'),
                    ('postponed', 'Postponed by Trainer'),
                    ('attended', 'Attended'),
                    ('no_show', 'Not Attended'),
                    ('cancelled', 'Cancelled'),
                    ('completed', 'Completed'),
                ],
                db_index=True,
                default='confirmed',
                max_length=20,
            ),
        ),
    ]
