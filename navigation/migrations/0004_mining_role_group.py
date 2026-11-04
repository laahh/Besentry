from django.db import migrations

NEW_ROLES = ('mining-bc',)


def forwards(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in NEW_ROLES:
        Group.objects.get_or_create(name=name)


def backwards(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.filter(name__in=NEW_ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('navigation', '0003_pengajuan_role_groups'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
