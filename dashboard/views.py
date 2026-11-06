from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.gis.db.models.functions import Area, Transform
from django.db.models import Sum
from django.shortcuts import redirect, render

from core.models import Zona

from . import coverage

# UTM zone 50N covers Berau, so areas come out in real square metres.
UTM_50N_SRID = 32650
SQ_M_PER_HECTARE = 10_000
LATEST_ZONA_LIMIT = 5


def _zona_with_area():
    return Zona.objects.annotate(luas=Area(Transform('geom', UTM_50N_SRID)))


def _to_hectare(area):
    return area.sq_m / SQ_M_PER_HECTARE if area else 0


@login_required
def index(request):
    """The landing page: Dashboard Coverage, or this summary for those who may not see it (contractors)."""
    if coverage.can_open(request.user):
        return redirect('coverage:index')
    total_luas = _zona_with_area().aggregate(total=Sum('luas'))['total']
    zona_terbaru = [
        {'pk': z.pk, 'nama': z.nama, 'luas_ha': _to_hectare(z.luas), 'dibuat': z.dibuat}
        for z in _zona_with_area().order_by('-dibuat')[:LATEST_ZONA_LIMIT]
    ]

    context = {
        'total_zona': Zona.objects.count(),
        'total_luas_ha': _to_hectare(total_luas),
        'total_pengguna': get_user_model().objects.filter(is_active=True).count(),
        'zona_terbaru': zona_terbaru,
    }
    return render(request, 'dashboard/index.html', context)
