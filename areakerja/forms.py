from django import forms

from cctv.pengajuan_forms import SHP_ZIP_MAX_MB

from .models import AreaKerja, Departemen, JenisArea


class SlotForm(forms.Form):
    jenis = forms.ChoiceField(choices=JenisArea.choices)
    sumber = forms.ChoiceField(choices=AreaKerja.Sumber.choices)
    file_shp = forms.FileField(required=False)

    def clean_file_shp(self):
        upload = self.cleaned_data.get('file_shp')
        if upload and upload.size > SHP_ZIP_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f'Maksimal {SHP_ZIP_MAX_MB} MB.')
        return upload


class AjukanForm(forms.Form):
    catatan = forms.CharField(required=False, max_length=2000)


class VerifikasiForm(forms.Form):
    departemen = forms.ChoiceField(choices=Departemen.choices)
    aksi = forms.ChoiceField(choices=(('setujui', 'Disetujui'), ('kembalikan', 'Kembalikan')))
    catatan = forms.CharField(required=False, max_length=2000)


class AnotasiForm(forms.Form):
    """A point (lon, lat), or a line / an area drawn on the map (geometri, GeoJSON)."""
    departemen = forms.ChoiceField(choices=Departemen.choices)
    lon = forms.FloatField(min_value=-180, max_value=180, required=False)
    lat = forms.FloatField(min_value=-90, max_value=90, required=False)
    geometri = forms.CharField(required=False, max_length=200_000)
    teks = forms.CharField(max_length=1000)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('geometri') and (cleaned.get('lon') is None or cleaned.get('lat') is None):
            raise forms.ValidationError('Pilih titik, garis, atau area di peta.')
        return cleaned
