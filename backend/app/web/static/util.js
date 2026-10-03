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

  /* Заменяет содержимое узла. Родной replaceChildren превращает null в текст «null», поэтому
     пустые значения (нет подписи, нет иконки) отбрасываем здесь, а не в каждом вызове. */
  Zo.fill = function (el, kids) {
    el.replaceChildren.apply(el, [].concat(kids).filter(function (kid) { return kid != null && kid !== false; }));
  };

  /* Phosphor Icons (regular, MIT, phosphoricons.com): контур залит, поэтому fill, а не stroke. */
  var ICONS = {
    users: 'M117.25,157.92a60,60,0,1,0-66.5,0A95.83,95.83,0,0,0,3.53,195.63a8,8,0,1,0,13.4,8.74,80,80,0,0,1,134.14,0,8,8,0,0,0,13.4-8.74A95.83,95.83,0,0,0,117.25,157.92ZM40,108a44,44,0,1,1,44,44A44.05,44.05,0,0,1,40,108Zm210.14,98.7a8,8,0,0,1-11.07-2.33A79.83,79.83,0,0,0,172,168a8,8,0,0,1,0-16,44,44,0,1,0-16.34-84.87,8,8,0,1,1-5.94-14.85,60,60,0,0,1,55.53,105.64,95.83,95.83,0,0,1,47.22,37.71A8,8,0,0,1,250.14,206.7Z',
    left: 'M165.66,202.34a8,8,0,0,1-11.32,11.32l-80-80a8,8,0,0,1,0-11.32l80-80a8,8,0,0,1,11.32,11.32L91.31,128Z',
    right: 'M181.66,133.66l-80,80a8,8,0,0,1-11.32-11.32L164.69,128,90.34,53.66a8,8,0,0,1,11.32-11.32l80,80A8,8,0,0,1,181.66,133.66Z',
    send: 'M227.32,28.68a16,16,0,0,0-15.66-4.08l-.15,0L19.57,82.84a16,16,0,0,0-2.49,29.8L102,154l41.3,84.87A15.86,15.86,0,0,0,157.74,248q.69,0,1.38-.06a15.88,15.88,0,0,0,14-11.51l58.2-191.94c0-.05,0-.1,0-.15A16,16,0,0,0,227.32,28.68ZM157.83,231.85l-.05.14,0-.07-40.06-82.3,48-48a8,8,0,0,0-11.31-11.31l-48,48L24.08,98.25l-.07,0,.14,0L216,40Z',
    check: 'M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z',
    lock: 'M208,80H176V56a48,48,0,0,0-96,0V80H48A16,16,0,0,0,32,96V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V96A16,16,0,0,0,208,80ZM96,56a32,32,0,0,1,64,0V80H96ZM208,208H48V96H208V208Z',
    x: 'M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z',
    plus: 'M224,128a8,8,0,0,1-8,8H136v80a8,8,0,0,1-16,0V136H40a8,8,0,0,1,0-16h80V40a8,8,0,0,1,16,0v80h80A8,8,0,0,1,224,128Z',
    clock: 'M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm64-88a8,8,0,0,1-8,8H128a8,8,0,0,1-8-8V72a8,8,0,0,1,16,0v48h48A8,8,0,0,1,192,128Z',
    off: 'M80,56V24a8,8,0,0,1,16,0V56a8,8,0,0,1-16,0Zm40,8a8,8,0,0,0,8-8V24a8,8,0,0,0-16,0V56A8,8,0,0,0,120,64Zm32,0a8,8,0,0,0,8-8V24a8,8,0,0,0-16,0V56A8,8,0,0,0,152,64Zm96,56v8a40,40,0,0,1-37.51,39.91,96.59,96.59,0,0,1-27,40.09H208a8,8,0,0,1,0,16H32a8,8,0,0,1,0-16H56.54A96.3,96.3,0,0,1,24,136V88a8,8,0,0,1,8-8H208A40,40,0,0,1,248,120ZM200,96H40v40a80.27,80.27,0,0,0,45.12,72h69.76A80.27,80.27,0,0,0,200,136Zm32,24a24,24,0,0,0-16-22.62V136a95.78,95.78,0,0,1-1.2,15A24,24,0,0,0,232,128Z',
    wait: 'M211.18,196.56,139.57,128l71.61-68.56a1.59,1.59,0,0,1,.13-.13A16,16,0,0,0,200,32H56A16,16,0,0,0,44.7,59.31l.12.13L116.43,128,44.82,196.56l-.12.13A16,16,0,0,0,56,224H200a16,16,0,0,0,11.32-27.31A1.59,1.59,0,0,1,211.18,196.56ZM56,48h0v0Zm144,0-72,68.92L56,48ZM56,208l72-68.92L200,208Z',
    warn: 'M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm-8-80V80a8,8,0,0,1,16,0v56a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,172Z',
  };
  Zo.icon = function (name, cls) {
    var span = document.createElement('span');
    span.style.display = 'contents';
    span.innerHTML = '<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"' +
      (cls ? ' class="' + cls + '"' : '') + '><path d="' + ICONS[name] + '"/></svg>';
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
