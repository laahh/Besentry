from django import forms

from cctv.pengajuan_forms import SHP_ZIP_MAX_MB

from .models import AreaKerja, JenisArea


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
    aksi = forms.ChoiceField(choices=(('approve', 'Approve'), ('return', 'Return to Mitra')))
    catatan = forms.CharField(required=False, max_length=2000)
