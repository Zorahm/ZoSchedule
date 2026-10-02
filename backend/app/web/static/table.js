/* Таблица: строки — студенты, столбцы — пары дня. Сверху время начала, под ним ячейка отметки.
   Касание ячейки: пусто -> П -> Н -> П. Долгое нажатие стирает отметку. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;
  var LETTER = { present: 'П', absent: 'Н' };
  var WORD = { present: 'присутствует', absent: 'отсутствует' };
  var HOLD_MS = 520;

  /** Подсчёт по дню: сколько отмечено и сколько Н, всего и по каждой паре. */
  function stats(day) {
    var result = { total: day.students.length * day.pairs.length, marked: 0, absent: 0, slots: {} };
    day.pairs.forEach(function (pair) { result.slots[pair.slot] = { marked: 0, absent: 0 }; });
    day.students.forEach(function (student) {
      day.pairs.forEach(function (pair) {
        var mark = student.marks[pair.slot];
        if (!mark) return;
        result.marked++;
        result.slots[pair.slot].marked++;
        if (mark === 'absent') { result.absent++; result.slots[pair.slot].absent++; }
      });
    });
    return result;
  }

  function cellLabel(student, pair, mark) {
    return student.name + ', ' + pair.number + ' пара: ' + (mark ? WORD[mark] : 'не отмечено');
  }

  function cell(student, pair) {
    var mark = student.marks[pair.slot] || null;
    var button = h('button', { class: 'cell' + (mark ? ' ' + mark : ''), 'data-s': student.id, 'data-p': pair.slot,
      'aria-label': cellLabel(student, pair, mark) }, mark ? LETTER[mark] : '');
    return h('td', null, button);
  }

  function head(pair, nowSlot) {
    var cls = 'pair-h' + (pair.slot === nowSlot ? ' now' : '') + (pair.lessons > 1 ? ' double' : '');
    return h('th', { class: cls, scope: 'col' },
      h('button', { 'data-pair': pair.slot, 'aria-pressed': 'false', 'aria-label': pair.number + ' пара, начало в ' + pair.start + ': выбрать для массовой отметки' },
        h('span', { class: 't' }, pair.start),
        h('span', { class: 'n' }, pair.number + ' пара')));
  }

  function build(day, nowSlot) {
    var table = h('table', { class: 'grid' },
      h('thead', null, h('tr', null,
        h('th', { class: 'who-h', scope: 'col' }, 'ФИО', h('b', { class: 'cnt' }, day.students.length)),
        day.pairs.map(function (pair) { return head(pair, nowSlot); }))),
      h('tbody', null, day.students.map(function (student, index) {
        return h('tr', { 'data-row': student.id },
          h('th', { class: 'who', scope: 'row' },
            h('button', { 'data-student': student.id }, h('i', null, index + 1), h('span', null, Zo.shortName(student.name)))),
          day.pairs.map(function (pair) { return cell(student, pair); }));
      })),
      h('tfoot', null, h('tr', null,
        h('th', { scope: 'row' }, 'Нет на паре'),
        day.pairs.map(function (pair) { return h('td', { class: 'sum', 'data-sum': pair.slot }); }))));
    var sheet = h('div', { class: 'sheet', role: 'region', 'aria-label': 'Таблица посещаемости', tabindex: '-1' }, table);
    sheet.addEventListener('scroll', function () {
      sheet.classList.toggle('x-scrolled', sheet.scrollLeft > 2);
    }, { passive: true });
    refresh(sheet, day);
    return sheet;
  }

  /** Перекрашивает одну ячейку на месте, без пересборки таблицы. */
  function paint(sheet, student, pair, mark) {
    var el = sheet.querySelector('.cell[data-s="' + student.id + '"][data-p="' + pair.slot + '"]');
    if (!el) return;
    el.className = 'cell' + (mark ? ' ' + mark : '');
    el.textContent = mark ? LETTER[mark] : '';
    el.setAttribute('aria-label', cellLabel(student, pair, mark));
  }

  /** Итоги под столбцами и красная фамилия у тех, кого нет весь день. */
  function refresh(sheet, day) {
    var all = stats(day);
    day.pairs.forEach(function (pair) {
      var box = sheet.querySelector('[data-sum="' + pair.slot + '"]');
      if (!box) return;
      var s = all.slots[pair.slot];
      var left = day.students.length - s.marked;
      Zo.fill(box, [
        h('b', { class: s.absent ? '' : 'zero' }, s.absent || '—'),
        left ? h('small', null, 'ещё ' + left) : null]);
    });
    day.students.forEach(function (student) {
      var row = sheet.querySelector('tr[data-row="' + student.id + '"] .who');
      if (!row) return;
      var gone = day.pairs.length > 0 && day.pairs.every(function (pair) { return student.marks[pair.slot] === 'absent'; });
      row.classList.toggle('all-absent', gone);
    });
    return all;
  }

  /** Касание и долгое нажатие на ячейках через одно делегирование. */
  function bind(sheet, on) {
    var timer = null, held = false, x0 = 0, y0 = 0;
    function stop() { clearTimeout(timer); timer = null; }
    sheet.addEventListener('pointerdown', function (e) {
      var el = e.target.closest('.cell');
      if (!el) return;
      held = false; x0 = e.clientX; y0 = e.clientY;
      timer = setTimeout(function () { held = true; on.clear(+el.dataset.s, el.dataset.p); }, HOLD_MS);
    });
    sheet.addEventListener('pointermove', function (e) {
      if (timer && Math.hypot(e.clientX - x0, e.clientY - y0) > 8) stop();
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (name) { sheet.addEventListener(name, stop); });
    sheet.addEventListener('contextmenu', function (e) { if (e.target.closest('.cell')) e.preventDefault(); });
    sheet.addEventListener('click', function (e) {
      var el = e.target.closest('.cell');
      if (el) {
        if (held) { held = false; return; }
        on.tap(+el.dataset.s, el.dataset.p);
        return;
      }
      var pair = e.target.closest('[data-pair]');
      if (pair) return on.pair(pair.dataset.pair);
      var who = e.target.closest('[data-student]');
      if (who) on.student(+who.dataset.student);
    });
  }

  /** Подсвечивает столбец выбранной пары (slot=null снимает выделение). */
  function select(sheet, slot) {
    sheet.querySelectorAll('.sel').forEach(function (el) { el.classList.remove('sel'); });
    sheet.querySelectorAll('[data-pair]').forEach(function (el) { el.setAttribute('aria-pressed', 'false'); });
    if (!slot) return;
    sheet.querySelectorAll('th[class~="pair-h"] [data-pair="' + slot + '"]').forEach(function (el) {
      el.setAttribute('aria-pressed', 'true');
      el.parentNode.classList.add('sel');
    });
    sheet.querySelectorAll('.cell[data-p="' + slot + '"]').forEach(function (el) { el.parentNode.classList.add('sel'); });
    sheet.querySelectorAll('[data-sum="' + slot + '"]').forEach(function (el) { el.classList.add('sel'); });
  }

  Zo.table = { select: select, build: build, paint: paint, refresh: refresh, bind: bind, stats: stats };
})();
