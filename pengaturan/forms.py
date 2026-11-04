from django import forms
from django.contrib.auth.models import Group

from cctv.forms import BootstrapMixin
from cctv.models import CakupanCctv
from navigation import roles


class RoleForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = ('name',)
        labels = {'name': 'Nama role'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built-in role names are referenced in code; renaming one would break access.
        if self.instance.name in roles.ALL_ROLES:
            self.fields['name'].disabled = True
            self.fields['name'].help_text = 'Role bawaan sistem, namanya tidak bisa diubah.'


class CakupanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CakupanCctv
        fields = ('site', 'perusahaan', 'bisa_ubah')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].empty_label = 'Semua site'
        self.fields['perusahaan'].empty_label = 'Semua perusahaan'


class BaseCakupanFormSet(forms.BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        if 'DELETE' in form.fields:
            form.fields['DELETE'].widget.attrs['class'] = 'form-check-input'

    def clean(self):
        super().clean()
        # The built-in unique check skips rows with an empty field, but here empty means "all".
        seen = set()
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            key = (form.cleaned_data.get('site'), form.cleaned_data.get('perusahaan'))
            if key in seen:
                raise forms.ValidationError('Ada Coverage CCTV site + perusahaan yang sama lebih dari sekali.')
            seen.add(key)


CakupanFormSet = forms.inlineformset_factory(
    Group, CakupanCctv, form=CakupanForm, formset=BaseCakupanFormSet, extra=0, can_delete=True,
)


class UserRolesForm(forms.Form):
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'), required=False, widget=forms.CheckboxSelectMultiple,
    )
