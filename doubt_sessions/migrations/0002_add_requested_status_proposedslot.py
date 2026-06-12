import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('doubt_sessions', '0001_initial'),
        ('courses', '0001_initial'),
    ]

    operations = [
        # Make slot nullable (sessions start without a slot)
        migrations.AlterField(
            model_name='doubtsession',
            name='slot',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='session',
                to='doubt_sessions.instructorslot',
            ),
        ),
        # Expand status choices
        migrations.AlterField(
            model_name='doubtsession',
            name='status',
            field=models.CharField(
                choices=[
                    ('requested', 'Requested'),
                    ('selected', 'Slots Proposed'),
                    ('confirmed', 'Confirmed'),
                    ('attended', 'Attended'),
                    ('no_show', 'No Show'),
                    ('cancelled', 'Cancelled'),
                    ('completed', 'Completed'),
                ],
                db_index=True,
                default='confirmed',
                max_length=20,
            ),
        ),
        # Add course FK
        migrations.AddField(
            model_name='doubtsession',
            name='course',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='doubt_sessions',
                to='courses.course',
            ),
        ),
        # Add request_message field
        migrations.AddField(
            model_name='doubtsession',
            name='request_message',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        # Create ProposedSlot model
        migrations.CreateModel(
            name='ProposedSlot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slot_datetime', models.DateTimeField()),
                ('is_selected', models.BooleanField(default=False)),
                ('session', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='proposed_slots',
                    to='doubt_sessions.doubtsession',
                )),
            ],
            options={
                'ordering': ['slot_datetime'],
            },
        ),
    ]
