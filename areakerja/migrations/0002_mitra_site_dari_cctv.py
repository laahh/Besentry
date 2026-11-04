"""Start Mitra Site from the site/perusahaan pairs that already have CCTV; the admin adds or retires the rest."""

from django.db import migrations


def from_cctv(apps, schema_editor):
    Cctv = apps.get_model('cctv', 'Cctv')
    MitraSite = apps.get_model('areakerja', 'MitraSite')
    pairs = Cctv.objects.filter(site__isnull=False, perusahaan__isnull=False).values_list('site_id', 'perusahaan_id').distinct()
    MitraSite.objects.bulk_create(
        [MitraSite(site_id=site_id, perusahaan_id=perusahaan_id) for site_id, perusahaan_id in pairs],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('areakerja', '0001_initial'),
        ('cctv', '0011_snapshotmingguan_remove_kalibrasikamera_cctv_and_more'),
    ]

    operations = [migrations.RunPython(from_cctv, migrations.RunPython.noop)]
