from django import forms

from . import coverage
from .forms import BootstrapMixin
from .models import Cctv, PengajuanCoverage

SCREENSHOT_MAX_MB = 5
# File signatures, so only real images are stored (no Pillow needed).
IMAGE_SIGNATURES = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')
WEBP = (b'RIFF', b'WEBP')


def is_image(upload):
    head = upload.read(12)
    upload.seek(0)
    return head.startswith(IMAGE_SIGNATURES) or (head[:4] == WEBP[0] and head[8:12] == WEBP[1])


class PengajuanForm(BootstrapMixin, forms.ModelForm):
    """New coverage request, or the revision of a returned one (cctv fixed)."""

    # Read-only on the page: the map is drawn for the CCTV picked in the previous step.
    no_cctv = forms.CharField(label='No. CCTV', max_length=40, widget=forms.TextInput(attrs={'readonly': True}))
    geom_json = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = PengajuanCoverage
        fields = ('jenis', 'tangkapan_layar', 'keterangan')
        labels = {'tangkapan_layar': 'Tangkapan layar CCTV'}
        widgets = {
            'keterangan': forms.Textarea(attrs={'rows': 3}),
            'tangkapan_layar': forms.ClearableFileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}),
        }

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.cctv = self.instance.cctv if self.instance.pk else None
        if self.cctv:
            self.fields['no_cctv'].initial = self.cctv.no_cctv
            self.fields['no_cctv'].disabled = True
            if self.instance.geom:
                self.fields['geom_json'].initial = self.instance.geom.geojson

    def clean_no_cctv(self):
        if self.cctv:
            return self.cctv.no_cctv
        no_cctv = self.cleaned_data['no_cctv'].strip()
        editable = self.scope.filter(Cctv.objects.select_related('spec'), edit=True)
        self.cctv = editable.filter(no_cctv__iexact=no_cctv).first()
        if self.cctv is None:
            raise forms.ValidationError('No. CCTV tidak ditemukan atau di luar cakupan ubah Anda.')
        if PengajuanCoverage.objects.filter(cctv=self.cctv).exclude(status=PengajuanCoverage.Status.SELESAI).exists():
            raise forms.ValidationError('CCTV ini masih punya pengajuan yang belum selesai.')
        return self.cctv.no_cctv

    def clean_tangkapan_layar(self):
        upload = self.cleaned_data.get('tangkapan_layar')
        if upload and hasattr(upload, 'content_type'):  # a new upload, not the stored file
            if upload.size > SCREENSHOT_MAX_MB * 1024 * 1024:
                raise forms.ValidationError(f'Maksimal {SCREENSHOT_MAX_MB} MB.')
            if not is_image(upload):
                raise forms.ValidationError('File harus gambar PNG, JPG, atau WEBP.')
        return upload

    def clean(self):
        cleaned = super().clean()
        jenis = cleaned.get('jenis')
        if not self.cctv or not jenis:
            return cleaned
        active, _ = coverage.active_coverage(self.cctv)
        if jenis == PengajuanCoverage.Jenis.DICABUT:
            if active is None:
                self.add_error('jenis', 'CCTV ini belum punya coverage aktif yang bisa dicabut.')
            cleaned['geom'] = None
            return cleaned
        if not cleaned.get('tangkapan_layar'):
            self.add_error('tangkapan_layar', 'Lampirkan tangkapan layar CCTV.')
        try:
            cleaned['geom'] = coverage.parse_geometry(cleaned.get('geom_json'))
            coverage.validate_geometry(cleaned['geom'], self.cctv)
        except coverage.GeometryError as exc:
            self.add_error('geom_json', str(exc))
        return cleaned

    def save(self, commit=True):
        self.instance.cctv = self.cctv
        self.instance.geom = self.cleaned_data.get('geom')
        return super().save(commit)


class ReviewForm(forms.Form):
    keputusan = forms.ChoiceField(choices=(('setujui', 'Setujui'), ('kembalikan', 'Kembalikan')))
    checklist = forms.MultipleChoiceField(
        choices=PengajuanCoverage.CHECKLIST_SGI + PengajuanCoverage.CHECKLIST_SAFETY, required=False,
    )
    catatan = forms.CharField(required=False, max_length=2000)
