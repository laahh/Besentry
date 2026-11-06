"""Built-in roles say what someone does, in plain names. Where they do it is the Coverage CCTV and the
master PIC, so the three IT roles (which all cover everything by default) become one.

Merging joins members, permissions and Coverage CCTV. A database that narrowed IT PAMA, IT MTL or IT BC
to different places should review the IT role afterwards: its members now share the merged coverage.
"""

from django.db import migrations

RENAMED = (
    ('admin-hazard-motion', 'Admin'),
    ('hazard-motion-it-pama', 'IT'),
    ('hazard-motion-it-mtl', 'IT'),
    ('hazard-motion-it-bc', 'IT'),
    ('mitra-only', 'Mitra'),
    ('survey-mitra', 'Survey Mitra'),
    ('survey-bc', 'Survey BC'),
    ('mining-bc', 'Mining BC'),
    ('safety-approval', 'Safety BC'),
)
# Going back, the first old name of a merged role takes its members.
RESTORED = (
    ('Admin', 'admin-hazard-motion'),
    ('IT', 'hazard-motion-it-pama'),
    ('Mitra', 'mitra-only'),
    ('Survey Mitra', 'survey-mitra'),
    ('Survey BC', 'survey-bc'),
    ('Mining BC', 'mining-bc'),
    ('Safety BC', 'safety-approval'),
)
RECREATED_BACK = ('hazard-motion-it-mtl', 'hazard-motion-it-bc')


def _merge_coverage(CakupanCctv, source, target):
    for rule in CakupanCctv.objects.filter(group=source):
        same = CakupanCctv.objects.filter(group=target, site_id=rule.site_id, perusahaan_id=rule.perusahaan_id).first()
        if same is None:
            CakupanCctv.objects.filter(pk=rule.pk).update(group=target)
            continue
        if rule.bisa_ubah and not same.bisa_ubah:
            CakupanCctv.objects.filter(pk=same.pk).update(bisa_ubah=True)
        CakupanCctv.objects.filter(pk=rule.pk).delete()


def _merge(apps, source, target):
    Membership = apps.get_model('auth', 'User').groups.through
    existing = set(Membership.objects.filter(group=target).values_list('user_id', flat=True))
    for row in Membership.objects.filter(group=source):
        if row.user_id not in existing:
            Membership.objects.create(user_id=row.user_id, group=target)
    target.permissions.add(*source.permissions.all())
    _merge_coverage(apps.get_model('cctv', 'CakupanCctv'), source, target)
    source.delete()


def _rename(apps, pairs):
    Group = apps.get_model('auth', 'Group')
    for old, new in pairs:
        source = Group.objects.filter(name=old).first()
        if source is None:
            continue
        target = Group.objects.filter(name=new).first()
        if target is None:
            Group.objects.filter(pk=source.pk).update(name=new)
        else:
            _merge(apps, source, target)
    for _, new in pairs:
        Group.objects.get_or_create(name=new)


def forwards(apps, schema_editor):
    _rename(apps, RENAMED)


def backwards(apps, schema_editor):
    _rename(apps, RESTORED)
    Group = apps.get_model('auth', 'Group')
    for name in RECREATED_BACK:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ('navigation', '0004_mining_role_group'),
        ('cctv', '0012_pengajuan_bc_tanpa_survey_mitra'),  # Coverage CCTV rows use the old names until then
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
