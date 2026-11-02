"""Roles (Django groups), their CCTV scopes, and which users hold them."""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cctv.access import create_pair_roles
from cctv.models import CakupanCctv
from navigation import roles

from .forms import CakupanFormSet, RoleForm, UserRolesForm

MANAGERS = (roles.ADMIN_HAZARD_MOTION,)
PER_PAGE = 50
MEMBERS_SHOWN = 100
SEARCH_MAX = 100


def _check_manager(user):
    if not roles.has_any_role(user, roles.user_group_names(user), MANAGERS):
        raise PermissionDenied


def _search(request):
    return request.GET.get('q', '').strip()[:SEARCH_MAX]


@login_required
def role_list(request):
    _check_manager(request.user)
    q = _search(request)
    groups = (
        Group.objects.order_by('name')
        .annotate(jumlah_user=Count('user', distinct=True))
        .prefetch_related(Prefetch(
            'cakupan_cctv', queryset=CakupanCctv.objects.select_related('site', 'perusahaan'),
        ))
    )
    if q:
        groups = groups.filter(name__icontains=q)
    page = Paginator(groups, PER_PAGE).get_page(request.GET.get('page'))
    return render(request, 'pengaturan/role_list.html', {
        'page_obj': page,
        'q': q,
        'system_roles': roles.ALL_ROLES,
        'full_access_roles': MANAGERS,
    })


def _role_form(request, group=None):
    _check_manager(request.user)
    data = request.POST or None
    is_full_access = group is not None and group.name in MANAGERS
    form = RoleForm(data, instance=group)
    # Full-access roles see everything; their scope rows are not edited.
    formset = None if is_full_access else CakupanFormSet(data, instance=group or Group(), prefix='cakupan')

    if request.method == 'POST' and form.is_valid() and (formset is None or formset.is_valid()):
        with transaction.atomic():
            saved = form.save()
            if formset is not None:
                formset.instance = saved
                formset.save()
        messages.success(request, f'Role {saved.name} disimpan.')
        return redirect('pengaturan:role_update', pk=saved.pk)

    members = []
    if group:
        members = group.user_set.order_by('username').only('pk', 'username', 'first_name', 'last_name')
    return render(request, 'pengaturan/role_form.html', {
        'group': group,
        'form': form,
        'formset': formset,
        'members': members[:MEMBERS_SHOWN],
        'jumlah_member': group.user_set.count() if group else 0,
        'is_full_access': is_full_access,
    })


@login_required
def role_create(request):
    return _role_form(request)


@login_required
def role_update(request, pk):
    return _role_form(request, get_object_or_404(Group, pk=pk))


@login_required
@require_POST
def role_delete(request, pk):
    _check_manager(request.user)
    group = get_object_or_404(Group, pk=pk)
    if group.name in roles.ALL_ROLES:
        messages.error(request, f'{group.name} adalah role bawaan sistem dan tidak bisa dihapus.')
        return redirect('pengaturan:role_update', pk=pk)
    group.delete()
    messages.success(request, f'Role {group.name} dihapus.')
    return redirect('pengaturan:role_list')


@login_required
@require_POST
def role_generate(request):
    """Create one role per site + perusahaan pair that has CCTV."""
    _check_manager(request.user)
    created, pairs = create_pair_roles(bisa_ubah=request.POST.get('bisa_ubah') == '1')
    if created:
        messages.success(request, f'{len(created)} role baru dibuat ({pairs - len(created)} sudah ada).')
    else:
        messages.info(request, f'Tidak ada role baru: {pairs} pasangan site + perusahaan sudah punya role.')
    return redirect('pengaturan:role_list')


@login_required
def user_list(request):
    _check_manager(request.user)
    q = _search(request)
    users = (
        get_user_model().objects.order_by('username')
        .only('pk', 'username', 'first_name', 'last_name', 'email', 'is_active', 'is_superuser', 'last_login')
        .prefetch_related(Prefetch('groups', queryset=Group.objects.only('pk', 'name').order_by('name')))
    )
    if q:
        users = users.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )
    role = request.GET.get('role', '')
    if role.isdigit():
        users = users.filter(groups__pk=int(role))
    return render(request, 'pengaturan/user_list.html', {
        'page_obj': Paginator(users, PER_PAGE).get_page(request.GET.get('page')),
        'q': q,
        'role_filter': Group.objects.filter(pk=role).first() if role.isdigit() else None,
    })


@login_required
def user_roles(request, pk):
    _check_manager(request.user)
    user = get_object_or_404(get_user_model().objects.select_related('karyawan__perusahaan'), pk=pk)
    # Unchecking every role posts no fields at all, so `request.POST or None` would ignore it.
    form = UserRolesForm(request.POST if request.method == 'POST' else None, initial={'roles': user.groups.all()})

    if request.method == 'POST' and form.is_valid():
        chosen = form.cleaned_data['roles']
        if user == request.user and not request.user.is_superuser and not any(
            group.name in MANAGERS for group in chosen
        ):
            # Keeps an admin from locking themselves out of this page.
            form.add_error('roles', 'Anda tidak bisa melepas role admin dari akun sendiri.')
        else:
            user.groups.set(chosen)
            messages.success(request, f'Role {user.get_username()} disimpan.')
            return redirect('pengaturan:user_list')

    return render(request, 'pengaturan/user_form.html', {
        'target': user,
        'karyawan': getattr(user, 'karyawan', None),
        'form': form,
        'system_roles': roles.ALL_ROLES,
    })
