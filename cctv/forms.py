from django import forms
from django.contrib.gis.geos import Point

from core.models import DetailLokasi

from .models import Cctv, CctvAkses, CctvSpec, ControlRoom


class BootstrapMixin:
    """Viho/Bootstrap classes on every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault('class', 'form-select')
            else:
                widget.attrs.setdefault('class', 'form-control')


class CctvForm(BootstrapMixin, forms.ModelForm):
    longitude = forms.FloatField(required=False, min_value=-180, max_value=180)
    latitude = forms.FloatField(required=False, min_value=-90, max_value=90)

    class Meta:
        model = Cctv
        fields = (
            'no_cctv', 'nama', 'site', 'perusahaan', 'kategori', 'fungsi', 'bentuk_instalasi', 'jenis',
            'status', 'kondisi', 'control_room', 'lokasi', 'detail_lokasi', 'lokasi_pemasangan',
            'kategori_area', 'kategori_aktivitas', 'connected', 'mirrored', 'auto_alert',
            'periode_update', 'verifikasi_ocr', 'keterangan',
        )
        widgets = {
            # type=date only accepts ISO values; the locale format would render it empty.
            'periode_update': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'keterangan': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        # Only the sites/perusahaan the user may assign; clean() checks the pair.
        for name in ('site', 'perusahaan'):
            ids = scope.editable_ids(f'{name}_id')
            if ids is not None:
                self.fields[name].queryset = self.fields[name].queryset.filter(pk__in=ids)
        self.fields['control_room'].queryset = ControlRoom.objects.select_related('site')
        # Detail lokasi can run into thousands: only the chosen lokasi's are rendered,
        # the page reloads the list when the lokasi changes.
        lokasi_id = self['lokasi'].value()
        self.fields['detail_lokasi'].queryset = (
            DetailLokasi.objects.filter(lokasi_id=lokasi_id) if lokasi_id else DetailLokasi.objects.none()
        )
        if self.instance.posisi:
            self.initial.setdefault('longitude', self.instance.posisi.x)
            self.initial.setdefault('latitude', self.instance.posisi.y)

    def clean(self):
        cleaned = super().clean()
        lon, lat = cleaned.get('longitude'), cleaned.get('latitude')
        if (lon is None) != (lat is None):
            raise forms.ValidationError('Isi longitude dan latitude, atau kosongkan keduanya.')
        site, perusahaan = cleaned.get('site'), cleaned.get('perusahaan')
        if site and perusahaan and not self.scope.allows(site.pk, perusahaan.pk, edit=True):
            raise forms.ValidationError(f'Anda tidak punya akses ubah untuk {site} - {perusahaan}.')
        detail = cleaned.get('detail_lokasi')
        if detail and detail.lokasi_id != getattr(cleaned.get('lokasi'), 'pk', None):
            self.add_error('detail_lokasi', 'Detail lokasi tidak termasuk lokasi yang dipilih.')
        return cleaned

    def save(self, commit=True):
        lon, lat = self.cleaned_data.get('longitude'), self.cleaned_data.get('latitude')
        self.instance.posisi = Point(lon, lat, srid=4326) if lon is not None else None
        return super().save(commit)


class CctvSpecForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CctvSpec
        exclude = ('cctv',)


class CctvAksesForm(BootstrapMixin, forms.ModelForm):
    # The stored password is never sent back to the browser; blank keeps it.
    password_baru = forms.CharField(
        label='Password', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Kosongkan jika tidak diubah.',
    )
    hapus_password = forms.BooleanField(label='Hapus password', required=False)

    class Meta:
        model = CctvAkses
        exclude = ('cctv', 'password')

    def save(self, commit=True):
        if self.cleaned_data.get('hapus_password'):
            self.instance.password = ''
        elif self.cleaned_data.get('password_baru'):
            self.instance.password = self.cleaned_data['password_baru']
        return super().save(commit)
