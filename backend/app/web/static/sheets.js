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
    return drawer;
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

  /** Подтверждение отправки: что уйдёт, что не отмечено, и что делать дальше. */
  function sendSheet(info, onSend) {
    var error = h('div', { class: 'err', role: 'alert' });
    var go = h('button', { class: 'btn wide', onclick: submit }, Zo.icon('send'), 'Прислать картинку');
    var drawer = show([
      h('div', null, h('div', { class: 'kicker mono' }, 'Для куратора'), h('h2', null, info.title)),
      h('p', { class: 'lead' }, 'Бот пришлёт вам в чат картинку со всей таблицей: ' + info.students + ' ' +
        Zo.plural(info.students, 'студент', 'студента', 'студентов') + ', ' + info.pairs + ' ' + Zo.plural(info.pairs, 'пара', 'пары', 'пар') +
        ', с названиями пар. Перешлите её куратору.'),
      info.left ? h('div', { class: 'warn' }, 'Не отмечено ' + info.left + ' ' + Zo.plural(info.left, 'ячейка', 'ячейки', 'ячеек') +
        ': на картинке они будут пустыми. Лучше сначала доотметить.') : null,
      error, go,
      h('button', { class: 'btn ghost wide', onclick: close }, 'Отмена'),
    ]);

    async function submit() {
      go.disabled = true; go.lastChild.textContent = 'Отправляю…'; error.textContent = '';
      try {
        await onSend();
      } catch (e) {
        error.textContent = e.message || 'Не удалось отправить';
        go.disabled = false; go.lastChild.textContent = 'Прислать картинку';
        return;
      }
      Zo.haptic('success');
      var tg = window.Telegram && window.Telegram.WebApp;
      drawer.replaceChildren.apply(drawer, [h('div', { class: 'grab' }),
        h('div', { class: 'done' }, h('span', { class: 'ok' }, Zo.icon('check')),
          h('h2', null, 'Картинка отправлена'),
          h('p', { class: 'lead' }, 'Она в вашем чате с ботом. Откройте её и перешлите куратору.')),
        tg && tg.close ? h('button', { class: 'btn wide', onclick: function () { tg.close(); } }, 'Перейти в чат') : null,
        h('button', { class: 'btn ghost wide', onclick: close }, 'Остаться в журнале')].filter(Boolean));
    }
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

  Zo.sheets = { send: sendSheet, open: show, close: close, pair: pairSheet, student: studentSheet, toast: toast, isOpen: function () { return !!open; }, onClose: null };
})();
