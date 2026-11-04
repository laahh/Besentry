"""PIC SGI becomes the master PIC of every stage; the existing PICs stay as Survey BC, for all sites."""

import django.db.models.deletion
from django.db import migrations, models


def existing_pics_cover_all_sites(apps, schema_editor):
    apps.get_model('notifikasi', 'Pic').objects.update(site=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_karyawan_no_hp'),
        ('notifikasi', '0003_alter_notifikasi_jenis'),
    ]

    operations = [
        migrations.RenameModel(old_name='PicSgi', new_name='Pic'),
        migrations.AlterModelOptions(
            name='pic',
            options={'ordering': ['tahap', 'nama'], 'verbose_name': 'PIC', 'verbose_name_plural': 'PIC'},
        ),
        migrations.AddField(
            model_name='pic',
            name='tahap',
            field=models.CharField(
                choices=[('it', 'IT'), ('mitra', 'Survey Mitra'), ('sgi', 'Survey BC (SGI)'), ('safety', 'Safety BC')],
                default='sgi', max_length=10, verbose_name='Tahap',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pic',
            name='perusahaan',
            field=models.ForeignKey(
                blank=True, help_text='Kosong = semua perusahaan.', null=True,
                on_delete=django.db.models.deletion.CASCADE, related_name='pic', to='core.perusahaan',
            ),
        ),
        migrations.AlterField(
            model_name='pic',
            name='karyawan',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pic',
                to='core.karyawan',
            ),
        ),
        migrations.AlterField(
            model_name='pic',
            name='site',
            field=models.ForeignKey(
                blank=True, help_text='Kosong = semua site.', null=True,
                on_delete=django.db.models.deletion.CASCADE, related_name='pic', to='core.site',
            ),
        ),
        migrations.AddConstraint(
            model_name='pic',
            constraint=models.UniqueConstraint(
                condition=models.Q(('karyawan__isnull', False)), fields=('karyawan', 'tahap', 'site', 'perusahaan'),
                name='pic_unik_per_tahap', nulls_distinct=False,
            ),
        ),
        # Last: PostgreSQL refuses schema changes while this update's deferred FK checks are pending.
        migrations.RunPython(existing_pics_cover_all_sites, migrations.RunPython.noop),
    ]
