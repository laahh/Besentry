from django import forms

from .models import PengajuanCoverage

SCREENSHOT_MAX_MB = 5
SHP_ZIP_MAX_MB = 20
# File signatures, so only real images are stored (no Pillow needed).
IMAGE_SIGNATURES = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')
WEBP = (b'RIFF', b'WEBP')
# Never kept in a draft: the password only travels when IT continues.
NOT_IN_DRAFT = ('csrfmiddlewaretoken', 'aksi', 'akses-password_baru', 'akses-hapus_password')


def is_image(upload):
    head = upload.read(12)
    upload.seek(0)
    return head.startswith(IMAGE_SIGNATURES) or (head[:4] == WEBP[0] and head[8:12] == WEBP[1])


TEXT_WIDGETS = (forms.TextInput, forms.NumberInput, forms.Textarea, forms.EmailInput, forms.URLInput)


def _shown(field, value):
    if value is None or value == '':
        return '-'
    if isinstance(field, forms.BooleanField):
        return 'Ya' if value else 'Tidak'
    if isinstance(field, forms.ModelChoiceField):
        return str(field.queryset.model._default_manager.filter(pk=value).first() or value)
    if getattr(field, 'choices', None):
        return str(dict(field.choices).get(value, value))
    return str(value)


def mark_previous(form):
    """The CCTV's stored values as placeholders, and a 'Sebelumnya' note on every field IT changed.

    Only for a form bound to an existing row; the password is never shown.
    """
    if form is None or not form.instance.pk:
        return
    for name, field in form.fields.items():
        if name in NOT_IN_DRAFT or name.endswith('password') or name in ('password_baru', 'hapus_password'):
            continue
        initial = form[name].initial
        shown = _shown(field, initial)
        if isinstance(field.widget, TEXT_WIDGETS) and shown != '-':
            field.widget.attrs['placeholder'] = shown
        if form.is_bound and field.has_changed(initial, form[name].data):
            field.sebelumnya = shown


def draft_data(post):
    """IT's form as JSON for PengajuanCoverage.data_draft."""
    return {key: post.getlist(key) for key in post if key not in NOT_IN_DRAFT}


class ItForm(forms.Form):
    """What the request itself needs from IT, next to the CCTV, spec and akses forms."""

    tangkapan_layar = forms.FileField(
        label='Tangkapan layar CCTV', required=False,
        widget=forms.ClearableFileInput(attrs={'accept': 'image/png,image/jpeg,image/webp', 'class': 'form-control'}),
    )
    keterangan = forms.CharField(
        label='Catatan IT', required=False, max_length=2000,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
    )
    aksi = forms.ChoiceField(choices=(('draft', 'Simpan draft'), ('lanjutkan', 'Lanjutkan Digitasi')))

    def __init__(self, *args, pengajuan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pengajuan = pengajuan

    def clean_tangkapan_layar(self):
        upload = self.cleaned_data.get('tangkapan_layar')
        if upload:
            if upload.size > SCREENSHOT_MAX_MB * 1024 * 1024:
                raise forms.ValidationError(f'Maksimal {SCREENSHOT_MAX_MB} MB.')
            if not is_image(upload):
                raise forms.ValidationError('File harus gambar PNG, JPG, atau WEBP.')
        return upload

    def clean(self):
        cleaned = super().clean()
        has_screenshot = cleaned.get('tangkapan_layar') or (self.pengajuan and self.pengajuan.tangkapan_layar)
        if cleaned.get('aksi') == 'lanjutkan' and not has_screenshot:
            self.add_error('tangkapan_layar', 'Upload tangkapan layar CCTV terbaru.')
        return cleaned


class DigitasiForm(forms.Form):
    geom_json = forms.CharField(required=False, widget=forms.HiddenInput)
    # Set by the page when the polygon came from an SHP; the polygon itself may be edited afterwards.
    nama_shp = forms.CharField(required=False, max_length=255, widget=forms.HiddenInput)
    catatan = forms.CharField(required=False, max_length=1000,
                              widget=forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}))
    aksi = forms.ChoiceField(choices=(('draft', 'Simpan draft'), ('kirim', 'Ajukan ke Survey BC')))


class DigitasiSgiForm(forms.Form):
    """PT Berau Coal: Survey BC's boundary and checklist in one form."""
    geom_json = forms.CharField(required=False, widget=forms.HiddenInput)
    nama_shp = forms.CharField(required=False, max_length=255, widget=forms.HiddenInput)
    checklist = forms.MultipleChoiceField(choices=PengajuanCoverage.CHECKLIST_SGI, required=False)
    catatan = forms.CharField(required=False, max_length=2000,
                              widget=forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}))
    aksi = forms.ChoiceField(choices=(('draft', 'Simpan draft'), ('safety', 'Lanjut ke Safety'), ('it', 'Return to IT')))


class CekGeometriForm(forms.Form):
    """The page asks for an SHP to be read, or the drawn polygon to be checked, before saving."""
    geom_json = forms.CharField(required=False)
    file_shp = forms.FileField(required=False)

    def clean_file_shp(self):
        upload = self.cleaned_data.get('file_shp')
        if upload and upload.size > SHP_ZIP_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f'Maksimal {SHP_ZIP_MAX_MB} MB.')
        return upload


class DigitasiMassalForm(CekGeometriForm):
    """One shapefile with many boundaries: checked first, then sent to Survey BC."""
    geom_json = None
    file_shp = forms.FileField(
        label='File SHP (.zip)',
        widget=forms.ClearableFileInput(attrs={'accept': '.zip', 'class': 'form-control'}),
    )
    aksi = forms.ChoiceField(choices=(('cek', 'Cek file'), ('kirim', 'Kirim ke Survey BC')))


class ReviewForm(forms.Form):
    aksi = forms.ChoiceField(choices=(
        ('safety', 'Lanjut ke Safety'), ('approve', 'Approve'),
        ('mitra', 'Return to Survey Mitra'), ('sgi', 'Return to SGI'), ('it', 'Return to IT'),
    ))
    checklist = forms.MultipleChoiceField(
        choices=PengajuanCoverage.CHECKLIST_SGI + PengajuanCoverage.CHECKLIST_SAFETY, required=False,
    )
    catatan = forms.CharField(required=False, max_length=2000)
