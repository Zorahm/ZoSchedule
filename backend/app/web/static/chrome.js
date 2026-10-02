/* Шапка, полоса дней, нижняя панель и заглушки вместо таблицы. Здесь только разметка:
   состояние и действия приходят из app.js. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;

  /** Какая пара идёт сейчас в выбранном дне: { now, next, over } по данным и московским часам. */
  function progress(day) {
    var minutes = Zo.nowMinutes(), idx = -1, next = null;
    day.pairs.forEach(function (pair, i) {
      if (minutes >= Zo.minutes(pair.start) && minutes < Zo.minutes(pair.end)) idx = i;
      else if (!next && minutes < Zo.minutes(pair.start)) next = pair;
    });
    return { now: idx >= 0 ? day.pairs[idx] : null, next: next, over: !next && idx < 0 };
  }

  function when(iso) {
    var date = iso.slice(0, 10), time = iso.slice(11, 16), today = Zo.today();
    if (date === today) return 'сегодня в ' + time;
    if (date === Zo.addDays(today, 1)) return 'завтра в ' + time;
    return 'в ' + Zo.weekdayIn(date) + ' в ' + time;
  }

  function statusNodes(data, onBack) {
    var day = data.day, nodes = [];
    if (day.state === 'locked') nodes.push(h('span', null, 'Откроется ' + when(day.opens_at)));
    else if (day.state === 'off') nodes.push(h('span', null, 'Пар нет'));
    else if (day.state === 'unpublished') nodes.push(h('span', null, 'Ещё не опубликовано'));
    else if (day.date === Zo.today()) {
      var p = progress(day);
      if (p.now) nodes.push(h('span', { class: 'live' }, 'Идёт ' + p.now.number + ' пара · до ' + p.now.end));
      else if (p.next && p.next.number > 1) nodes.push(h('span', null, 'Перерыв · ' + p.next.number + ' пара в ' + p.next.start));
      else if (p.next) nodes.push(h('span', null, 'Начало в ' + p.next.start));
      else nodes.push(h('span', null, 'Пары закончились'));
    } else nodes.push(h('span', null, day.date < Zo.today() ? 'Прошедший день' : 'Впереди'));
    if (day.date === data.current_day && data.next_opens_at && day.date !== Zo.today()) {
      nodes.push(h('span', null, 'Дальше ' + when(data.next_opens_at)));
    }
    if (day.date !== data.current_day) {
      nodes.push(h('button', { class: 'back', onclick: onBack }, 'К ' + Zo.weekdayIn(data.current_day)));
    }
    return nodes;
  }

  function chip(entry, selected, onPick) {
    var cls = 'chip ' + entry.state + (entry.date === selected ? ' sel' : '') + (entry.date === Zo.today() ? ' today' : '');
    var share = entry.total ? Math.min(100, Math.round(entry.marked / entry.total * 100)) : 0;
    return h('button', { class: cls, onclick: function () { onPick(entry.date); },
      'aria-label': Zo.weekday(entry.date) + ', ' + Zo.dayLabel(entry.date), 'aria-pressed': entry.date === selected ? 'true' : 'false' },
      h('span', { class: 'w' }, Zo.weekdayShort(entry.date)),
      h('span', { class: 'd' }, Zo.parseDay(entry.date).getDate()),
      h('span', { class: 'foot' },
        entry.state === 'locked' ? Zo.icon('lock', 'lk') : null,
        entry.state === 'open' && entry.total ? h('span', { class: 'bar' }, h('i', { class: share === 100 ? 'full' : '', style: 'width:' + share + '%' })) : null));
  }

  function header(data, save, on) {
    var day = data.day;
    return h('header', { class: 'top' },
      h('div', { class: 'eyebrow' },
        h('span', { class: 'grp' }, data.group + ' · ', h('em', null, 'журнал')),
        h('span', { class: 'sp' }),
        h('span', { class: 'save', 'data-s': save, role: 'status' },
          save === 'offline' ? 'нет связи' : save === 'saving' ? 'сохраняю' : 'сохранено'),
        data.day.state === 'open' && day.students.length
          ? h('button', { class: 'icon-btn', 'aria-label': 'Отправить куратору', onclick: on.send }, Zo.icon('send')) : null,
        h('button', { class: 'icon-btn', 'aria-label': 'Список группы', onclick: on.roster }, Zo.icon('users'))),
      h('div', { class: 'title-row' },
        h('h1', null, Zo.weekday(day.date)),
        h('span', { class: 'date' }, Zo.dayLabel(day.date))),
      h('div', { class: 'status' }, statusNodes(data, on.back)),
      h('nav', { class: 'strip', 'aria-label': 'Дни недели' },
        h('button', { class: 'arrow', 'aria-label': 'Прошлая неделя', onclick: function () { on.week(-7); } }, Zo.icon('left')),
        data.week.map(function (entry) { return chip(entry, day.date, on.pick); }),
        h('button', { class: 'arrow', 'aria-label': 'Следующая неделя', onclick: function () { on.week(7); } }, Zo.icon('right'))));
  }

  /** Строка над панелью: какая пара выбрана, чтобы «Остальным П» не ударило по всему дню. */
  function scope(pair, on) {
    var detail = [pair.teacher || 'преподаватель не указан на сайте', pair.room ? 'ауд. ' + pair.room : null].filter(Boolean).join(' · ');
    return h('div', { class: 'scope' },
      h('button', { class: 'scope-text', onclick: on.open, 'aria-label': 'Подробнее о паре' },
        h('span', { class: 'k' }, pair.number + ' пара · ' + pair.start + '–' + pair.end),
        h('span', { class: 't' }, pair.title),
        h('span', { class: 'm' }, detail)),
      h('button', { class: 'icon-btn', 'aria-label': 'Снять выбор пары', onclick: on.clear }, Zo.icon('x')));
  }

  function bar(stats, mode, on) {
    var left = stats.total - stats.marked;
    var share = stats.total ? Math.round(stats.marked / stats.total * 100) : 0;
    var sub = left ? 'осталось ' + left : (stats.absent ? 'Н: ' + stats.absent : 'все на месте');
    function seg(value, letter, label) {
      return h('button', { class: 'seg ' + (value === 'present' ? 'p' : 'n'), role: 'radio', 'aria-label': label,
        'aria-checked': mode === value ? 'true' : 'false', onclick: function () { on.mode(value); } }, letter);
    }
    return h('footer', { class: 'bar-b' },
      h('div', { class: 'prog' },
        h('div', { class: 'num' }, stats.marked, h('span', null, ' из ' + stats.total)),
        h('div', { class: 'track' }, h('i', { class: share === 100 ? 'full' : '', style: 'width:' + share + '%' })),
        h('div', { class: 'sub' }, sub)),
      left ? h('div', { class: 'seg-group', role: 'radiogroup', 'aria-label': 'Чем заполнить остальных' },
        seg('present', 'П', 'Присутствуют'), seg('absent', 'Н', 'Отсутствуют')) : null,
      left
        ? h('button', { class: 'btn fill ' + (mode === 'absent' ? 'n' : 'p'), onclick: on.fill }, 'Остальным ' + (mode === 'absent' ? 'Н' : 'П'))
        : on.scoped
          ? h('button', { class: 'btn', disabled: true }, 'Пара отмечена')
          : h('button', { class: 'btn send', onclick: on.send }, Zo.icon('send'), 'Отправить куратору'));
  }

  function card(icon, title, text, button) {
    return h('div', { class: 'state' }, h('div', { class: 'card' },
      Zo.icon(icon, 'big-i'), h('div', { class: 'big' }, title), text ? h('p', null, text) : null, button || null));
  }

  function plan(day) {
    if (!day.pairs.length) return null;
    return h('ol', { class: 'plan' }, day.pairs.map(function (pair) {
      return h('li', null, h('time', null, pair.start), h('span', null, pair.title));
    }));
  }

  /** Вместо таблицы: день закрыт, выходной, не опубликован, пуст список группы или сбой. */
  function placeholder(data, on) {
    var day = data.day;
    if (day.state === 'locked') {
      var state = card('wait', 'Журнал откроется с первой парой',
        'Как только начнётся ' + day.pairs[0].number + ' пара в ' + day.pairs[0].start + ', отметки на весь день можно будет ставить сразу.');
      var list = plan(day);
      if (list) state.append(list);
      return state;
    }
    if (day.state === 'off') return card('off', 'Выходной', 'В этот день у группы нет пар.');
    if (day.state === 'unpublished') return card('clock', 'Ещё не опубликовано', 'Колледж пока не выложил расписание на этот день.');
    return card('users', 'Список группы пуст',
      'Добавьте ФИО студентов один раз: дальше таблица сама заполнится парами каждого дня.',
      h('button', { class: 'btn', onclick: on.roster }, 'Добавить список'));
  }

  function failure(error, onRetry) {
    var denied = error.status === 401 || error.status === 403;
    var title = denied ? 'Журнал только для старосты' : 'Не получилось загрузить';
    var text = error.code === 'expired' ? 'Сессия устарела. Закройте журнал и откройте его заново из чата с ботом.' : error.message;
    return h('div', { class: 'state', style: 'margin:0 16px' },
      h('div', { class: 'card' }, Zo.icon(denied ? 'lock' : 'clock', 'big-i'), h('div', { class: 'big' }, title), h('p', null, text),
        denied ? null : h('button', { class: 'btn', onclick: onRetry }, 'Повторить')));
  }

  Zo.chrome = { header: header, scope: scope, bar: bar, placeholder: placeholder, failure: failure, progress: progress };
})();
