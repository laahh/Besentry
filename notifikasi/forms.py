from django import forms

from cctv.forms import BootstrapMixin
from cctv.models import Cctv
from core.karyawan_sync import karyawan_by_sid
from core.models import Perusahaan

from .models import Pic, normalise_wa


class PicForm(BootstrapMixin, forms.ModelForm):
    """Assign an employee to a stage of the change request, for a site and perusahaan.

    Picked by SID; name, email and WhatsApp come from the SID employee data and may be
    overridden (e.g. a work number that SID does not have).
    """

    sid = forms.CharField(
        label='Karyawan (SID)', max_length=20,
        help_text='Cari nama atau SID; data diambil dari data karyawan SID.',
    )

    class Meta:
        model = Pic
        fields = ('tahap', 'no_wa', 'email', 'site', 'perusahaan', 'aktif')
        help_texts = {'no_wa': 'Terisi dari SID; ubah jika nomor WhatsApp-nya berbeda.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].empty_label = 'Semua site'
        self.fields['perusahaan'].empty_label = 'Semua perusahaan'
        # Perusahaan also holds every SID company; only those with CCTV can be assigned.
        with_cctv = Perusahaan.objects.filter(pk__in=Cctv.objects.values('perusahaan_id'))
        self.fields['perusahaan'].queryset = with_cctv | Perusahaan.objects.filter(pk=self.instance.perusahaan_id)
        self.karyawan = self.instance.karyawan if self.instance.pk else None
        if self.karyawan:
            self.fields['sid'].initial = self.karyawan.sid
        for name in ('no_wa', 'email'):
            self.fields[name].required = False

    def clean_sid(self):
        sid = self.cleaned_data['sid'].strip().upper()
        if self.karyawan and self.karyawan.sid == sid:
            return sid
        self.karyawan = karyawan_by_sid(sid)
        if self.karyawan is None:
            raise forms.ValidationError('SID tidak ditemukan di data karyawan.')
        return sid

    def clean(self):
        cleaned = super().clean()
        if self.karyawan:
            # Blank fields take the SID values; typed ones win.
            cleaned['email'] = cleaned.get('email') or self.karyawan.email
            cleaned['no_wa'] = normalise_wa(cleaned.get('no_wa') or self.karyawan.no_hp)
            if not cleaned['email'] and not cleaned['no_wa']:
                raise forms.ValidationError('Karyawan ini tidak punya nomor HP maupun email di SID. Isi salah satunya.')
            same = Pic.objects.filter(karyawan=self.karyawan, tahap=cleaned.get('tahap'), site=cleaned.get('site'),
                                      perusahaan=cleaned.get('perusahaan')).exclude(pk=self.instance.pk)
            if same.exists():
                raise forms.ValidationError('Karyawan ini sudah menjadi PIC untuk tahap dan cakupan yang sama.')
        return cleaned

    def save(self, commit=True):
        self.instance.karyawan = self.karyawan
        self.instance.nama = self.karyawan.nama
        self.instance.no_wa = self.cleaned_data['no_wa']
        self.instance.email = self.cleaned_data['email']
        return super().save(commit)
