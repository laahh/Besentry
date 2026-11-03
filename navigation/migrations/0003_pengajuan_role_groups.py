from django.db import migrations

NEW_ROLES = ('hazard-motion-it-bc', 'survey-mitra')
# SGI is called Survey BC in the request flow; the role follows.
RENAMED = (('sgi', 'survey-bc'),)


def forwards(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for old, new in RENAMED:
        if not Group.objects.filter(name=new).exists():
            Group.objects.filter(name=old).update(name=new)
        Group.objects.get_or_create(name=new)
    for name in NEW_ROLES:
        Group.objects.get_or_create(name=name)


def backwards(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=NEW_ROLES).delete()
    for old, new in RENAMED:
        Group.objects.filter(name=new).update(name=old)


class Migration(migrations.Migration):
    dependencies = [
        ('navigation', '0002_registrasi_role_groups'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
