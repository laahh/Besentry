from django.db import migrations

ROLES = (
    'sgi',
    'safety-approval',
)


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in ROLES:
        Group.objects.get_or_create(name=name)


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('navigation', '0001_create_role_groups'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
