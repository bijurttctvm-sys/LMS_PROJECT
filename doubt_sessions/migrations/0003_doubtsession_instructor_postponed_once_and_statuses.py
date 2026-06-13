from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('doubt_sessions', '0002_add_requested_status_proposedslot'),
    ]

    operations = [
        migrations.AddField(
            model_name='doubtsession',
            name='instructor_postponed_once',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='doubtsession',
            name='status',
            field=models.CharField(
                choices=[
                    ('requested', 'Requested'),
                    ('selected', 'Slots Proposed'),
                    ('confirmed', 'Confirmed'),
                    ('postponed', 'Postponed by Instructor'),
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
