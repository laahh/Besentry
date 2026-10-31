from django.db import migrations

ROLES = (
    'admin-hazard-motion',
    'hazard-motion-it-pama',
    'hazard-motion-it-mtl',
    'mitra-only',
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
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
