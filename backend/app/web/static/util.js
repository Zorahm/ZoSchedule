/* Мелкие помощники: разметка, иконки, даты, часы по Москве, вибрация Telegram. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});

  /** h('div', {class: 'a', onclick: fn}, 'текст', child) */
  Zo.h = function (tag, attrs) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === false || value == null) return;
      if (key.slice(0, 2) === 'on') el.addEventListener(key.slice(2), value);
      else if (key === 'text') el.textContent = value;
      else el.setAttribute(key, value === true ? '' : value);
    });
    Array.prototype.slice.call(arguments, 2).forEach(function add(kid) {
      if (kid == null || kid === false) return;
      if (Array.isArray(kid)) kid.forEach(add);
      else el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    });
    return el;
  };

  var ICONS = {
    users: '<path d="M16 19v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 17.5V19M10 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 10 11zm9 8v-1.5a3.5 3.5 0 0 0-2.4-3.3M14.6 4.8a3.2 3.2 0 0 1 0 6.1"/>',
    left: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
    right: '<path d="M9.5 5.5 16 12l-6.5 6.5"/>',
    send: '<path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5 21 3z"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2.2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    off: '<path d="M4 12h16M8 8l-4 4 4 4"/>',
    wait: '<path d="M5 4h14M5 20h14M7 4v3.5a5 5 0 0 0 2.2 4.1L12 12l2.8.4A5 5 0 0 0 17 7.5V4M7 20v-3.5a5 5 0 0 1 2.2-4.1L12 12l2.8-.4A5 5 0 0 1 17 16.5V20"/>',
  };
  Zo.icon = function (name, cls) {
    var span = document.createElement('span');
    span.style.display = 'contents';
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' +
      (cls ? ' class="' + cls + '"' : '') + '>' + ICONS[name] + '</svg>';
    return span.firstChild;
  };

  var WEEKDAYS = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
  var WEEKDAYS_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var WEEKDAYS_TO = ['воскресенью', 'понедельнику', 'вторнику', 'среде', 'четвергу', 'пятнице', 'субботе'];
  var WEEKDAYS_IN = ['воскресенье', 'понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу'];

  /** "2026-10-02" -> локальная полночь; часовые пояса тут не нужны, это просто календарь. */
  Zo.parseDay = function (iso) {
    var p = iso.split('-');
    return new Date(+p[0], +p[1] - 1, +p[2]);
  };
  Zo.dayIso = function (date) {
    var m = date.getMonth() + 1, d = date.getDate();
    return date.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (d < 10 ? '0' : '') + d;
  };
  Zo.addDays = function (iso, n) {
    var date = Zo.parseDay(iso);
    date.setDate(date.getDate() + n);
    return Zo.dayIso(date);
  };
  Zo.weekday = function (iso) { return WEEKDAYS[Zo.parseDay(iso).getDay()]; };
  Zo.weekdayShort = function (iso) { return WEEKDAYS_SHORT[Zo.parseDay(iso).getDay()]; };
  Zo.weekdayTo = function (iso) { return WEEKDAYS_TO[Zo.parseDay(iso).getDay()]; };
  Zo.weekdayIn = function (iso) { return WEEKDAYS_IN[Zo.parseDay(iso).getDay()]; };
  Zo.dayLabel = function (iso) { var d = Zo.parseDay(iso); return d.getDate() + ' ' + MONTHS[d.getMonth()]; };
  Zo.monday = function (iso) {
    var date = Zo.parseDay(iso);
    return Zo.addDays(iso, -((date.getDay() + 6) % 7));
  };

  /** "HH:MM" -> минуты от полуночи. */
  Zo.minutes = function (clock) {
    var p = clock.split(':');
    return +p[0] * 60 + +p[1];
  };

  /* Московские часы. Время телефона не годится: оно может уйти или стоять в другом поясе.
     Берём "сейчас" из ответа сервера и дальше отсчитываем по таймеру страницы. */
  var clock = { date: null, minutes: 0, at: 0 };
  Zo.syncClock = function (nowIso) {
    // "2026-10-02T11:20:00+03:00": дата и время уже московские, смещение не нужно.
    clock.date = nowIso.slice(0, 10);
    clock.minutes = +nowIso.slice(11, 13) * 60 + +nowIso.slice(14, 16);
    clock.at = Date.now();
  };
  Zo.nowMinutes = function () {
    return clock.minutes + (Date.now() - clock.at) / 60000;
  };
  Zo.today = function () { return clock.date; };

  Zo.haptic = function (kind) {
    var tg = window.Telegram && window.Telegram.WebApp;
    var feedback = tg && tg.HapticFeedback;
    if (!feedback) return;
    try {
      if (kind === 'select') feedback.selectionChanged();
      else feedback.notificationOccurred(kind);
    } catch (e) { /* вибрация не обязательна */ }
  };

  /** "Абрамов Илья Сергеевич" -> "Абрамов И." */
  Zo.shortName = function (full) {
    var parts = full.trim().split(/\s+/);
    return parts.length < 2 ? parts[0] : parts[0] + ' ' + parts[1].charAt(0) + '.';
  };

  /** Окончание: 1 пара, 2 пары, 5 пар. */
  Zo.plural = function (n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  };
})();
