"""One timeline of changes for a CCTV, merged from the Cctv, CctvSpec and CctvAkses history."""

from collections import defaultdict

from django.contrib.auth import get_user_model

from .models import Cctv, CctvAkses, CctvSpec

TYPES = {'+': 'Dibuat', '~': 'Diubah', '-': 'Dihapus'}
SOURCES = (
    (Cctv, 'id', ''),
    (CctvSpec, 'cctv_id', 'Spesifikasi'),
    (CctvAkses, 'cctv_id', 'Akses'),
)


def _display(field, value):
    if value in (None, ''):
        return '-'
    if field.choices:
        return dict(field.flatchoices).get(value, value)
    if field.get_internal_type() == 'PointField':
        return f'{value.x:.6f}, {value.y:.6f}'
    if isinstance(value, bool):
        return 'Ya' if value else 'Tidak'
    return value


def riwayat(cctv_id, limit=30):
    """Newest first. Each entry: date, user, type, section and the changed fields."""
    entries = []
    for model, key, section in SOURCES:
        # One extra row so the oldest shown change can still be diffed.
        rows = list(model.history.filter(**{key: cctv_id}).order_by('-history_date')[:limit + 1])
        for index, row in enumerate(rows[:limit]):
            previous = rows[index + 1] if index + 1 < len(rows) else None
            changes = []
            if previous and row.history_type == '~':
                changes = row.diff_against(previous).changes
            entries.append({'row': row, 'model': model, 'section': section, 'changes': changes})

    entries.sort(key=lambda e: e['row'].history_date, reverse=True)
    entries = entries[:limit]
    return _resolve(entries)


def _resolve(entries):
    """Turn user ids and foreign key ids into names, one query per related model."""
    wanted = defaultdict(set)
    for entry in entries:
        for change in entry['changes']:
            field = entry['model']._meta.get_field(change.field)
            if field.is_relation:
                wanted[field.related_model].update(v for v in (change.old, change.new) if v)
    names = {model: model.objects.in_bulk(ids) for model, ids in wanted.items()}
    users = get_user_model().objects.in_bulk({e['row'].history_user_id for e in entries} - {None})

    result = []
    for entry in entries:
        row, model = entry['row'], entry['model']
        changes = []
        for change in entry['changes']:
            field = model._meta.get_field(change.field)
            old, new = change.old, change.new
            if field.is_relation:
                old, new = (names[field.related_model].get(v, v) if v else None for v in (old, new))
            changes.append({
                'field': field.verbose_name,
                'old': _display(field, old),
                'new': _display(field, new),
            })
        user = users.get(row.history_user_id)
        result.append({
            'date': row.history_date,
            'user': user.get_username() if user else 'Sistem',
            'type': TYPES[row.history_type],
            'section': entry['section'],
            'reason': row.history_change_reason or '',
            'changes': changes,
        })
    return result
