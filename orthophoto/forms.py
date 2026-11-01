import datetime

from django import forms
from django.template.defaultfilters import filesizeformat

from . import services


def parse_bulan(value):
    """'2026-11' -> date(2026, 11, 1)."""
    try:
        return datetime.datetime.strptime(value, '%Y-%m').date()
    except (TypeError, ValueError):
        raise ValueError('Bulan harus berformat YYYY-MM.') from None


class PublishForm(forms.Form):
    file_sumber = forms.ChoiceField(label='File mentah')
    bulan = forms.CharField(label='Bulan', widget=forms.TextInput(attrs={'type': 'month'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only files actually in the incoming folder can be picked, so no path ever comes from the browser.
        self.fields['file_sumber'].choices = [
            (p.name, f'{p.name} ({filesizeformat(p.stat().st_size)})') for p in services.incoming_files()
        ]

    def clean_bulan(self):
        try:
            return parse_bulan(self.cleaned_data['bulan'])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
