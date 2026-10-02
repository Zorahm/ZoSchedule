/* Шторки снизу (пара, студент) и тост с «Отменить». */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;
  var open = null, toastTimer = null;

  function close() {
    if (!open) return;
    open.remove();
    open = null;
    if (Zo.sheets.onClose) Zo.sheets.onClose();
  }

  function show(children) {
    close();
    var scrim = h('div', { class: 'scrim', onclick: close });
    var drawer = h('div', { class: 'drawer', role: 'dialog', 'aria-modal': 'true' }, h('div', { class: 'grab' }), children);
    open = h('div', null, scrim, drawer);
    document.body.append(open);
    var first = drawer.querySelector('.act');
    if (first) first.focus({ preventScroll: true });
  }

  function actions(onPick, labels) {
    function act(cls, letter, text, value) {
      return h('button', { class: 'act ' + cls, onclick: function () { close(); onPick(value); } }, h('b', null, letter), text);
    }
    return h('div', { class: 'actions' },
      act('p', 'П', labels[0], 'present'),
      act('n', 'Н', labels[1], 'absent'),
      act('', '×', labels[2], null));
  }

  function tally(count, total, absent) {
    return h('div', { class: 'tally' }, 'Отмечено ' + count + ' из ' + total + (absent ? ' · Н: ' + absent : ''));
  }

  function pairSheet(pair, s, studentsCount, onPick) {
    var kind = pair.kind ? pair.kind.charAt(0).toUpperCase() + pair.kind.slice(1) : null;
    show([
      h('div', null,
        h('div', { class: 'kicker mono' }, pair.number + ' пара · ' + pair.start + '–' + pair.end),
        h('h2', null, pair.title)),
      h('div', { class: 'meta' },
        kind ? h('span', null, kind) : null,
        pair.teacher ? h('span', null, pair.teacher) : h('span', { class: 'unknown' }, 'Преподаватель не указан на сайте'),
        pair.room ? h('span', null, 'Аудитория ' + pair.room) : null,
        pair.lessons > 1 ? h('span', { class: 'unknown' }, 'В этот час на сайте две пары, отметка одна на обе') : null),
      actions(onPick, ['Все были', 'Никого', 'Сбросить']),
      tally(s.marked, studentsCount, s.absent),
    ]);
  }

  function studentSheet(student, pairsCount, marked, absent, onPick) {
    show([
      h('div', null,
        h('div', { class: 'kicker mono' }, 'Студент'),
        h('h2', null, student.name)),
      actions(onPick, ['Был весь день', 'Нет весь день', 'Сбросить']),
      tally(marked, pairsCount, absent),
    ]);
  }

  function toast(text, undo) {
    clearTimeout(toastTimer);
    var old = document.querySelector('.toast');
    if (old) old.remove();
    var el = h('div', { class: 'toast', role: 'status' }, h('span', null, text),
      undo ? h('button', { onclick: function () { el.remove(); undo(); } }, 'Отменить') : null);
    document.body.append(el);
    toastTimer = setTimeout(function () { el.remove(); }, undo ? 6000 : 2600);
  }

  Zo.sheets = { open: show, close: close, pair: pairSheet, student: studentSheet, toast: toast, isOpen: function () { return !!open; }, onClose: null };
})();
