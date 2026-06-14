from django.db import migrations


def promote_superusers_to_admin_role(apps, schema_editor):
    User = apps.get_model('users', 'User')
    User.objects.filter(is_superuser=True).exclude(role='admin').update(role='admin')


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(
            promote_superusers_to_admin_role,
            migrations.RunPython.noop,
        ),
    ]
