"""PT Berau Coal requests skip Survey Mitra: any still waiting there move to Survey BC's combined step."""

from django.db import migrations

KODE_BERAU_COAL = 'BC'


def to_survey_bc(apps, schema_editor):
    PengajuanCoverage = apps.get_model('cctv', 'PengajuanCoverage')
    PengajuanCoverage.objects.filter(
        cctv__perusahaan__kode=KODE_BERAU_COAL, status__in=['menunggu_mitra', 'return_mitra'],
    ).update(status='menunggu_sgi')


class Migration(migrations.Migration):
    dependencies = [
        ('cctv', '0011_snapshotmingguan_remove_kalibrasikamera_cctv_and_more'),
    ]

    operations = [migrations.RunPython(to_survey_bc, migrations.RunPython.noop)]
