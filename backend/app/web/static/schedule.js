/* Расписание: день крупно, неделя полосой, весь год вперёд через календарь.
   Данные подгружаются по месяцам и копятся в S.days; рисуем всегда из кэша. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;

  var POLL_MS = 120000, SWIPE_PX = 56, GAP_MIN = 30;
  var S = { days: {}, loaded: {}, sel: null, today: null, group: '', headman: false, publishedTo: null, min: null, max: null };
  var root, top, list, started = false, swipe = null;

  /* ---------- границы и месяцы ---------- */

  function monthKey(iso) { return iso.slice(0, 7); }
  function monthStart(key) { return key + '-01'; }
  function monthEnd(key) { var p = key.split('-'); return Zo.dayIso(new Date(+p[0], +p[1], 0)); }
  function shiftMonth(key, n) { var p = key.split('-'), d = new Date(+p[0], +p[1] - 1 + n, 1); return Zo.dayIso(d).slice(0, 7); }

  /** Назад два месяца, вперёд двенадцать: «весь год» от сегодня. */
  function setBounds() {
    S.min = monthStart(shiftMonth(monthKey(S.today), -2));
    S.max = monthEnd(shiftMonth(monthKey(S.today), 12));
  }

  async function ensure(key) {
    if (S.loaded[key]) return;
    var data = await Zo.api.schedule(monthStart(key), monthEnd(key));
    Zo.syncClock(data.now);
    S.group = data.group; S.headman = data.headman; S.publishedTo = data.published_to;
    data.days.forEach(function (day) { S.days[day.date] = day; });
    S.loaded[key] = true;
  }

  function ensureWeek(iso) {
    var monday = Zo.monday(iso), keys = {};
    keys[monthKey(monday)] = 1; keys[monthKey(Zo.addDays(monday, 6))] = 1;
    return Promise.all(Object.keys(keys).map(ensure));
  }

  /* ---------- пометки и тексты ---------- */

  function count(n) { return n + ' ' + Zo.plural(n, 'пара', 'пары', 'пар'); }
  function clock(min) { var hrs = Math.floor(min / 60), m = min % 60; return (hrs ? hrs + ' ч ' : '') + (m ? m + ' мин' : '').trim(); }

  function changeText(c) {
    if (c.kind === 'added') return 'Добавлена';
    if (c.kind === 'moved') {
      var parts = ['Перенесена' + (c.from_time && c.from_time !== c.time ? ' с ' + c.from_time : '')];
      if (c.from_room && c.to_room && c.from_room !== c.to_room) parts.push('ауд. ' + c.from_room + ' → ' + c.to_room);
      return parts.join(' · ');
    }
    if (c.kind === 'teacher_changed') return 'Другой преподаватель' + (c.from_teacher ? ' · был ' + Zo.shortName(c.from_teacher) : '');
    return 'Отменена';
  }

  function changeOf(day, lesson) {
    return day.changes.filter(function (c) { return c.kind !== 'cancelled' && c.discipline === lesson.title && c.time === lesson.start; })[0];
  }

  /* ---------- карточки ---------- */

  function lessonCard(day, lesson, live) {
    var change = changeOf(day, lesson);
    return h('li', { class: 'les' + (live ? ' now' : '') },
      h('div', { class: 'les-top mono' }, h('time', null, lesson.start + ' – ' + lesson.end),
        lesson.room ? h('span', { class: 'room' }, (lesson.building ? lesson.building + ' · ' : '') + 'ауд. ' + lesson.room) : null),
      h('div', { class: 'les-title' }, lesson.title),
      h('div', { class: 'les-meta' },
        lesson.teacher ? h('span', null, lesson.teacher) : h('span', { class: 'unknown' }, 'преподаватель не указан на сайте'),
        h('span', { class: 'kind' }, lesson.kind)),
      lesson.stream.length ? h('div', { class: 'les-meta' }, h('span', null, 'Поток: ' + lesson.stream.join(', '))) : null,
      h('div', { class: 'tags' },
        live ? h('span', { class: 'tag live' }, 'Идёт сейчас') : null,
        lesson.exam ? h('span', { class: 'tag exam' }, lesson.kind) : null,
        change ? h('span', { class: 'tag warn' }, changeText(change)) : null));
  }

  function cancelledCard(change) {
    return h('li', { class: 'les cancelled' },
      h('div', { class: 'les-top mono' }, h('time', null, change.time)),
      h('div', { class: 'les-title' }, change.discipline),
      h('div', { class: 'tags' }, h('span', { class: 'tag gone' }, 'Отменена')));
  }

  function gapRow(minutes) { return h('li', { class: 'gap mono', 'aria-label': 'Окно ' + clock(minutes) }, 'окно ' + clock(minutes)); }

  function lessonItems(day) {
    var live = null;
    if (day.date === Zo.today()) {
      var now = Zo.nowMinutes();
      day.lessons.forEach(function (l) { if (Zo.minutes(l.start) <= now && now < Zo.minutes(l.end)) live = l; });
    }
    var items = day.lessons.map(function (l) { return { time: l.start, node: lessonCard(day, l, l === live), lesson: l }; });
    day.changes.filter(function (c) { return c.kind === 'cancelled'; }).forEach(function (c) {
      items.push({ time: c.time, node: cancelledCard(c), lesson: null });
    });
    items.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : (a.lesson ? 0 : 1) - (b.lesson ? 0 : 1); });
    var out = [], prev = null;
    items.forEach(function (item) {
      if (item.lesson && prev) {
        var gap = Zo.minutes(item.lesson.start) - Zo.minutes(prev.end);
        if (gap >= GAP_MIN) out.push(gapRow(gap));
      }
      if (item.lesson) prev = item.lesson;
      out.push(item.node);
    });
    return out;
  }

  function emptyState(day) {
    if (day.state === 'unpublished') {
      return h('div', { class: 'void dashed' }, h('div', { class: 'void-t' }, 'Ещё не опубликовано'),
        h('p', null, S.publishedTo ? 'Сайт колледжа показывает расписание до ' + Zo.dayLabel(S.publishedTo) + '. Дальше пар пока нет, но это не выходной.'
          : 'Сайт колледжа ещё не выложил расписание на эту дату. Это не выходной.'));
    }
    if (day.state === 'nodata') return h('div', { class: 'void' }, h('div', { class: 'void-t' }, 'Нет данных'), h('p', null, 'Расписание за этот день бот не застал.'));
    var hasGone = day.changes.some(function (c) { return c.kind === 'cancelled'; });
    return hasGone ? null : h('div', { class: 'void' }, h('div', { class: 'void-t' }, 'Выходной'), h('p', null, 'В этот день пар нет.'));
  }

  /* ---------- шапка и полоса недели ---------- */

  function stripDay(iso) {
    var day = S.days[iso], mark = '';
    if (day && day.changes.length) mark = 'chg'; else if (day && day.state === 'lessons') mark = 'has';
    var unusable = !day || day.state === 'unpublished' || day.state === 'nodata';
    return h('button', { class: 'wd ' + mark + (iso === S.sel ? ' sel' : '') + (iso === Zo.today() ? ' today' : '') + (unusable ? ' dim' : ''),
      'aria-pressed': iso === S.sel ? 'true' : 'false', 'aria-label': Zo.weekday(iso) + ', ' + Zo.dayLabel(iso), onclick: function () { select(iso); } },
      h('span', { class: 'w' }, Zo.weekdayShort(iso)), h('span', { class: 'n mono' }, Zo.parseDay(iso).getDate()), h('i'));
  }

  function drawTop() {
    var day = S.days[S.sel], monday = Zo.monday(S.sel), days = [];
    for (var i = 0; i < 7; i++) days.push(stripDay(Zo.addDays(monday, i)));
    var sub = Zo.dayLabel(S.sel) + (day && day.state === 'lessons' ? ' · ' + count(day.lessons.length) : '');
    Zo.fill(top, h('div', { class: 'top' },
      h('div', { class: 'eyebrow' },
        h('span', { class: 'grp' }, 'Группа · ', h('em', null, S.group)), h('span', { class: 'sp' }),
        S.sel !== Zo.today() ? h('button', { class: 'pill', onclick: function () { select(Zo.today()); } }, 'Сегодня') : null,
        h('button', { class: 'icon-btn', 'aria-label': 'Календарь на год', onclick: openCalendar }, Zo.line('calendar'))),
      h('div', { class: 'title' }, h('h1', null, Zo.weekday(S.sel)), h('div', { class: 'sub' }, sub)),
      h('div', { class: 'wk' },
        h('button', { class: 'step', 'aria-label': 'Прошлая неделя', onclick: function () { select(Zo.addDays(S.sel, -7)); } }, Zo.line('prev')),
        h('div', { class: 'wk-days' }, days),
        h('button', { class: 'step', 'aria-label': 'Следующая неделя', onclick: function () { select(Zo.addDays(S.sel, 7)); } }, Zo.line('next')))));
  }

  function drawList() {
    var day = S.days[S.sel], items = day.state === 'lessons' || day.changes.length ? lessonItems(day) : [], empty = emptyState(day);
    Zo.fill(list, [h('ul', { class: 'lessons' }, items), empty, footnote(day)]);
  }

  function footnote(day) {
    if (day.state !== 'lessons' || !S.publishedTo) return null;
    return h('div', { class: 'foot-note mono' }, 'Опубликовано на сайте до ' + Zo.dayLabel(S.publishedTo));
  }

  function render() { drawTop(); drawList(); }

  /* ---------- действия ---------- */

  async function select(iso, quiet) {
    if (iso < S.min) iso = S.min;
    if (iso > S.max) iso = S.max;
    try { await ensureWeek(iso); } catch (e) { if (!quiet) Zo.sheets.toast(e.message); return; }
    if (!quiet && iso !== S.sel) Zo.haptic('select');
    S.sel = iso;
    render();
    if (!quiet) list.scrollTop = 0;
  }

  function openCalendar() {
    Zo.calendar.open({ state: S, ensure: ensure, pick: function (iso) { Zo.sheets.close(); select(iso); } });
  }

  function onTouch(event) {
    var t = event.changedTouches[0];
    if (event.type === 'touchstart') { swipe = { x: t.clientX, y: t.clientY }; return; }
    if (!swipe) return;
    var dx = t.clientX - swipe.x, dy = t.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) > SWIPE_PX && Math.abs(dy) < Math.abs(dx) / 2) select(Zo.addDays(S.sel, dx < 0 ? 1 : -1));
  }

  function failure(error, retry) {
    var outside = error.status === 401;
    list.replaceChildren(h('div', { class: 'void' }, h('div', { class: 'void-t' }, outside ? 'Откройте из Telegram' : 'Не получилось загрузить'),
      h('p', null, error.message), outside ? null : h('button', { class: 'btn', onclick: retry }, 'Повторить')));
  }

  /* ---------- запуск ---------- */

  async function start() {
    try {
      S.loaded = {}; S.days = {};
      var first = await Zo.api.schedule();
      Zo.syncClock(first.now);
      S.today = Zo.today(); S.group = first.group; S.headman = first.headman;
      setBounds();
      await select(S.sel && S.sel >= S.min ? S.sel : S.today, true);
      started = true;
      if (Zo.shell) Zo.shell.headman(S.headman);
    } catch (e) {
      top.replaceChildren();
      failure(e, start);
    }
  }

  /** Тихое обновление: новые снимки приходят сами, экран не прыгает. */
  async function refresh() {
    if (!started || document.hidden) return;
    var keys = Object.keys(S.loaded);
    S.loaded = {};
    try { await Promise.all(keys.map(ensure)); if (S.sel) { var was = list.scrollTop; render(); list.scrollTop = was; } }
    catch (e) { keys.forEach(function (k) { S.loaded[k] = true; }); /* оставляем показанное */ }
  }

  function boot(el) {
    root = el;
    top = h('div', { class: 'slot-top' });
    list = h('main', { class: 'sch-list' });
    list.addEventListener('touchstart', onTouch, { passive: true });
    list.addEventListener('touchend', onTouch, { passive: true });
    root.append(top, list);
    setInterval(refresh, POLL_MS);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
    start();
  }

  Zo.schedule = { boot: boot, state: S, monthKey: monthKey, monthStart: monthStart, monthEnd: monthEnd, shiftMonth: shiftMonth };
})();
