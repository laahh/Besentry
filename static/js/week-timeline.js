// WeekTimeline: a time slider of weeks (Monday to Sunday, ISO numbering), in the style of a historical
// imagery slider. Stand-alone: it builds its own markup, needs only static/css/week-timeline.css, and
// knows nothing about the data behind the weeks. Made for MapLibre pages but does not depend on it.
//
//   import { WeekTimeline } from 'besentry/week-timeline';
//   const slider = new WeekTimeline(container, {
//     weeks: [{ minggu: '2026-10-26', status: 'terkunci' }, { minggu: '2026-11-02', status: 'live' }],
//     selected: '2026-11-02',
//     onPick: (minggu) => loadWeek(minggu),       // a week was chosen by the user
//   });
//   slider.update(weeks, selected);               // new data (e.g. after a filter change)
//   slider.select('2026-10-26');                  // move to a week from code (no onPick)
//
// weeks: consecutive Mondays, oldest first, as 'YYYY-MM-DD'. status: any key of `statuses` (below);
// 'kosong' and 'menunggu' draw a hollow dot. The selected week always sits under the centre marker.
// The line is padded with faded weeks before the first and after the last, as far as the screen is
// wide; those are never picked, dragged to or chosen in the calendar. Drag (mouse or touch), click,
// scroll, arrow keys, the buttons (one week, first, last) and the calendar popup move along it; the
// buttons turn grey at either end.

const TICK = 46; // px per week
const BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const BULAN_PANJANG = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September',
  'Oktober', 'November', 'Desember'];
const HARI = [['S', 'Senin'], ['S', 'Selasa'], ['R', 'Rabu'], ['K', 'Kamis'], ['J', 'Jumat'], ['S', 'Sabtu'], ['M', 'Minggu']];

export const STATUS = {
  live: { label: 'Week berjalan · live', tone: 'live' },
  terkunci: { label: 'Terkunci · snapshot', tone: 'netral' },
  menunggu: { label: 'Menunggu data', tone: 'kosong', hollow: true },
  kosong: { label: 'Belum ada angka', tone: 'kosong', hollow: true },
};

// Feather icons (MIT), inline so the component needs no icon library.
const ICON = {
  first: '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/>',
  prev: '<polyline points="15 18 9 12 15 6"/>',
  next: '<polyline points="9 18 15 12 9 6"/>',
  last: '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/>'
    + '<line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name]}</svg>`;

const pad = (n) => String(n).padStart(2, '0');
const parseDay = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
// ISO 8601: weeks start on Monday; week 1 holds the year's first Thursday.
export function isoWeek(monday) {
  const thursday = addDays(monday, 3);
  const firstThursday = new Date(thursday.getFullYear(), 0, 4);
  const firstMonday = addDays(firstThursday, -((firstThursday.getDay() + 6) % 7));
  return { week: 1 + Math.round((monday - firstMonday) / (7 * 864e5)), year: thursday.getFullYear() };
}
const dayMonth = (d) => `${pad(d.getDate())} ${BULAN[d.getMonth()]}`;

function describe(week, extra = {}) {
  const monday = parseDay(week.minggu);
  const sunday = addDays(monday, 6);
  const { week: no, year } = isoWeek(monday);
  return {
    ...week, ...extra, monday, w: `W${pad(no)}`, label: `W${pad(no)} ${year}`,
    rentang: `${dayMonth(monday)} – ${dayMonth(sunday)} ${sunday.getFullYear()}`,
  };
}

export class WeekTimeline {
  constructor(container, { weeks, selected, onPick = () => {}, statuses = STATUS } = {}) {
    this.container = container;
    this.onPick = onPick;
    this.statuses = statuses;
    this.shift = 0;
    this.fill = 0;
    this.drag = null;
    this.render();
    this.bind();
    this.update(weeks, selected);
  }

  // --- Markup ------------------------------------------------------------------------------------

  render() {
    const button = (name, title) => `<button type="button" class="wt-btn" data-wt="${name}" title="${title}" aria-label="${title}">${svg(name)}</button>`;
    this.container.classList.add('wt');
    this.container.tabIndex = 0;
    this.container.innerHTML = `
      <div class="wt-head">
        ${button('first', 'Week pertama')}${button('prev', 'Week sebelumnya')}
        <span class="wt-label"></span><span class="wt-status"></span>
        <span class="wt-spacer"></span>
        ${button('next', 'Week berikutnya')}${button('last', 'Week terakhir')}${button('calendar', 'Pilih dari kalender')}
      </div>
      <div class="wt-view">
        <div class="wt-marker" aria-hidden="true"></div>
        <div class="wt-rail wt-months"></div>
        <div class="wt-rail wt-weeks"></div>
      </div>
      <div class="wt-cal" hidden>
        <div class="wt-cal-head">${button('prev', 'Bulan sebelumnya').replace('data-wt="prev"', 'data-wt="cal-prev"')}
          <span class="wt-cal-month"></span>${button('next', 'Bulan berikutnya').replace('data-wt="next"', 'data-wt="cal-next"')}</div>
        <table><thead><tr><th>Week</th>${HARI.map(([d, name]) => `<th title="${name}">${d}</th>`).join('')}</tr></thead><tbody></tbody></table>
      </div>`;
    const $ = (selector) => this.container.querySelector(selector);
    this.el = {
      label: $('.wt-label'), status: $('.wt-status'), view: $('.wt-view'), months: $('.wt-months'), weeks: $('.wt-weeks'),
      cal: $('.wt-cal'), calMonth: $('.wt-cal-month'), calBody: $('.wt-cal tbody'),
      first: $('[data-wt="first"]'), prev: $('[data-wt="prev"]'), next: $('[data-wt="next"]'), last: $('[data-wt="last"]'),
    };
  }

  // --- Data --------------------------------------------------------------------------------------

  update(weeks, selected) {
    this.real = (weeks || []).map((week) => describe(week));
    if (!this.real.length) return;
    this.pick = this.real.some((w) => w.minggu === selected) ? selected : this.real[this.real.length - 1].minggu;
    this.build();
  }

  select(minggu) {
    this.choose(minggu, { silent: true });
  }

  pickable(week) { return Boolean(week) && !week.isi; }
  get first() { return this.fill; }
  get last() { return this.fill + this.real.length - 1; }
  index() { return Math.max(this.first, this.list.findIndex((w) => w.minggu === this.pick)); }
  needed() { return Math.ceil((this.el.view.clientWidth || window.innerWidth) / 2 / TICK) + 1; }

  // `real` with faded filler weeks on both sides, enough to reach the edges of the line.
  padded() {
    this.fill = this.needed();
    const first = this.real[0].monday;
    const last = this.real[this.real.length - 1].monday;
    const filler = (monday, later) => describe({ minggu: isoDay(monday), status: '' }, { isi: true, later });
    return [
      ...Array.from({ length: this.fill }, (_, i) => filler(addDays(first, -7 * (this.fill - i)), false)),
      ...this.real,
      ...Array.from({ length: this.fill }, (_, i) => filler(addDays(last, 7 * (i + 1)), true)),
    ];
  }

  build() {
    this.list = this.padded();
    const { months, weeks } = this.el;
    months.replaceChildren();
    weeks.replaceChildren();
    let run = null;
    for (const week of this.list) {
      const key = `${week.monday.getFullYear()}-${week.monday.getMonth()}`;
      if (!run || run.key !== key) {
        run = { key, el: document.createElement('div'), n: 0 };
        run.el.className = 'wt-month';
        months.append(run.el);
      }
      run.n += 1;
      run.el.style.width = `${run.n * TICK}px`;
      // A month with one week on the line has room for its name only.
      const name = BULAN[week.monday.getMonth()];
      run.el.textContent = run.n > 1 ? `${name} ${week.monday.getFullYear()}` : name;

      const tick = document.createElement('button');
      tick.type = 'button';
      const hollow = week.isi || this.statuses[week.status]?.hollow;
      tick.className = `wt-tick${hollow ? ' wt-hollow' : ''}${week.isi ? ' wt-filler' : ''}${week.minggu === this.pick ? ' wt-picked' : ''}`;
      tick.title = week.isi ? `${week.label} · ${week.later ? 'belum berjalan' : 'belum ada data'}` : `${week.label} · ${week.rentang}`;
      tick.disabled = Boolean(week.isi);
      tick.dataset.minggu = week.minggu;
      tick.innerHTML = `<span class="wt-dot"></span><small>${week.w}</small>`;
      weeks.append(tick);
    }
    this.header();
    this.centre(false);
  }

  header() {
    const week = this.list[this.index()];
    if (!week) return;
    this.el.label.textContent = `${week.label} · ${week.rentang}`;
    const status = this.statuses[week.status] || STATUS.kosong;
    this.el.status.textContent = status.label;
    this.el.status.className = `wt-status wt-tone-${status.tone}`;
    // At either end the buttons that would go further turn grey.
    const atStart = this.index() <= this.first;
    const atEnd = this.index() >= this.last;
    this.el.first.disabled = atStart;
    this.el.prev.disabled = atStart;
    this.el.next.disabled = atEnd;
    this.el.last.disabled = atEnd;
  }

  // --- Moving ------------------------------------------------------------------------------------

  centred(i) { return this.el.view.clientWidth / 2 - (i + 0.5) * TICK; }
  under(px) {
    const i = Math.round((this.el.view.clientWidth / 2 - px) / TICK - 0.5);
    return Math.max(this.first, Math.min(this.last, i));
  }
  move(px, animate = true) {
    this.shift = px;
    for (const rail of [this.el.months, this.el.weeks]) {
      rail.style.transition = animate ? '' : 'none';
      rail.style.transform = `translateX(${px}px)`;
    }
  }
  centre(animate = true) { this.move(this.centred(this.index()), animate); }

  choose(minggu, { silent = false } = {}) {
    if (!this.pickable(this.list.find((w) => w.minggu === minggu))) return;
    const changed = minggu !== this.pick;
    this.pick = minggu;
    for (const tick of this.el.weeks.children) tick.classList.toggle('wt-picked', tick.dataset.minggu === minggu);
    this.header();
    this.centre();
    this.el.cal.hidden = true;
    if (changed && !silent) this.onPick(minggu);
  }
  step(n) { this.choose(this.list[Math.max(this.first, Math.min(this.last, this.index() + n))].minggu); }

  bind() {
    const { view } = this.el;
    const on = (name, handler) => this.container.querySelector(`[data-wt="${name}"]`).addEventListener('click', handler);
    on('first', () => this.choose(this.list[this.first].minggu));
    on('last', () => this.choose(this.list[this.last].minggu));
    on('prev', () => this.step(-1));
    on('next', () => this.step(1));
    on('calendar', () => this.toggleCalendar());
    on('cal-prev', () => this.drawCalendar(-1));
    on('cal-next', () => this.drawCalendar(1));

    // Drag slides the line like a scrubber; on release the week under the marker is picked.
    // A press without movement is a click: the week clicked comes to the centre.
    view.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      this.drag = { id: event.pointerId, x: event.clientX, start: this.shift, moved: false };
      event.preventDefault(); // no text selection or native button drag
    });
    view.addEventListener('pointermove', (event) => {
      const { drag } = this;
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 4) return;
      if (!drag.moved) {
        drag.moved = true;
        view.setPointerCapture(event.pointerId);
        view.classList.add('wt-dragging');
      }
      this.move(Math.max(this.centred(this.last), Math.min(this.centred(this.first), drag.start + dx)), false);
    });
    const release = (event) => {
      const { drag } = this;
      if (!drag || event.pointerId !== drag.id) return;
      this.drag = null;
      view.classList.remove('wt-dragging');
      if (drag.moved) {
        this.choose(this.list[this.under(this.shift)].minggu);
      } else {
        const tick = event.target.closest('.wt-tick');
        if (tick) this.choose(tick.dataset.minggu);
      }
    };
    view.addEventListener('pointerup', release);
    view.addEventListener('pointercancel', release);

    // The wheel steps one week at a time and picks it after a short pause.
    let wheelTimer = null;
    view.addEventListener('wheel', (event) => {
      event.preventDefault();
      const target = Math.max(this.first, Math.min(this.last, this.under(this.shift) + ((event.deltaY || event.deltaX) > 0 ? 1 : -1)));
      this.move(this.centred(target));
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => this.choose(this.list[this.under(this.shift)].minggu), 350);
    }, { passive: false });
    this.container.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); this.step(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); this.step(1); }
      if (event.key === 'Escape') this.el.cal.hidden = true;
    });
    // A wider line (entering fullscreen, a bigger screen) needs more filler weeks.
    new ResizeObserver(() => {
      if (!this.real?.length) return;
      if (this.needed() > this.fill) this.build();
      else this.centre(false);
    }).observe(view);
    document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement) this.el.cal.hidden = true; });
  }

  // --- Calendar popup: weeks outside the line's data are faded and cannot be picked ---------------

  toggleCalendar() {
    this.el.cal.hidden = !this.el.cal.hidden;
    if (this.el.cal.hidden) return;
    const day = parseDay(this.pick);
    this.calMonth = new Date(day.getFullYear(), day.getMonth(), 1);
    this.drawCalendar(0);
  }

  drawCalendar(monthStep) {
    this.calMonth = new Date(this.calMonth.getFullYear(), this.calMonth.getMonth() + monthStep, 1);
    const month = this.calMonth;
    this.el.calMonth.textContent = `${BULAN_PANJANG[month.getMonth()]} ${month.getFullYear()}`;
    const known = new Set(this.real.map((w) => w.minggu));
    const rows = [];
    let monday = addDays(month, -((month.getDay() + 6) % 7));
    while (monday.getMonth() === month.getMonth() || addDays(monday, 6).getMonth() === month.getMonth()) {
      const key = isoDay(monday);
      const cells = [`<td>W${pad(isoWeek(monday).week)}</td>`];
      for (let i = 0; i < 7; i += 1) {
        const day = addDays(monday, i);
        cells.push(`<td class="${day.getMonth() === month.getMonth() ? '' : 'wt-other'}">${day.getDate()}</td>`);
      }
      rows.push(`<tr data-minggu="${key}" class="${known.has(key) ? 'wt-can' : 'wt-out'}${key === this.pick ? ' wt-seen' : ''}">${cells.join('')}</tr>`);
      monday = addDays(monday, 7);
    }
    this.el.calBody.innerHTML = rows.join('');
    for (const row of this.el.calBody.querySelectorAll('tr.wt-can')) {
      row.addEventListener('click', () => this.choose(row.dataset.minggu));
    }
  }
}
