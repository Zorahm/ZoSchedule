/* Календарь на год вперёд: шторка с сеткой месяца. Дни с парами и с изменениями помечены
   точками, дни за пределами опубликованного расписания приглушены и подписаны. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;
  var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var HEAD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];

  function open(ctx) {
    var S = ctx.state, sch = Zo.schedule, key = sch.monthKey(S.sel), drawer = null, body = null;

    function cell(iso) {
      var day = S.days[iso], mark = day && day.state === 'lessons' ? ' has' : '';
      var dim = !day || day.state === 'unpublished' || day.state === 'nodata' ? ' dim' : '';
      return h('button', { class: 'cd' + mark + dim + (iso === S.sel ? ' sel' : '') + (iso === Zo.today() ? ' today' : ''),
        'aria-label': Zo.weekday(iso) + ', ' + Zo.dayLabel(iso), onclick: function () { ctx.pick(iso); } },
        h('span', { class: 'mono' }, Zo.parseDay(iso).getDate()), h('i'));
    }

    function grid() {
      var first = sch.monthStart(key), last = sch.monthEnd(key), blanks = (Zo.parseDay(first).getDay() + 6) % 7, cells = [];
      for (var b = 0; b < blanks; b++) cells.push(h('span'));
      for (var iso = first; iso <= last; iso = Zo.addDays(iso, 1)) cells.push(cell(iso));
      return h('div', { class: 'cal-grid' }, HEAD.map(function (d) { return h('b', { class: 'mono' }, d); }), cells);
    }

    function draw() {
      var p = key.split('-'), lo = sch.monthKey(S.min), hi = sch.monthKey(S.max);
      var published = S.publishedTo ? 'Опубликовано на сайте до ' + Zo.dayLabel(S.publishedTo) + '. Дальше: «Ещё не опубликовано».' : null;
      Zo.fill(body, [
        h('div', { class: 'cal-head' },
          h('button', { class: 'icon-btn', 'aria-label': 'Прошлый месяц', disabled: key <= lo, onclick: function () { go(-1); } }, Zo.line('prev')),
          h('div', { class: 'cal-title' }, MONTHS[+p[1] - 1], ' ', h('span', { class: 'mono' }, p[0])),
          h('button', { class: 'icon-btn', 'aria-label': 'Следующий месяц', disabled: key >= hi, onclick: function () { go(1); } }, Zo.line('next'))),
        grid(),
        h('div', { class: 'cal-legend mono' }, h('span', { class: 'lg has' }, 'есть пары'), h('span', { class: 'lg dim' }, 'не опубликовано')),
        published ? h('div', { class: 'cal-note' }, published) : null,
      ]);
    }

    /** Месяц переключается сразу, данные подтягиваются следом: быстрые нажатия не теряются. */
    async function go(step) {
      var next = key = sch.shiftMonth(key, step);
      draw();
      Zo.haptic('select');
      try { await ctx.ensure(next); } catch (e) { Zo.sheets.toast(e.message); return; }
      if (key === next) draw();
    }

    body = h('div', { class: 'cal' });
    drawer = Zo.sheets.open([h('div', null, h('div', { class: 'kicker mono' }, 'Расписание на год'), h('h2', null, 'Выберите день')), body]);
    draw();
    return drawer;
  }

  Zo.calendar = { open: open };
})();
