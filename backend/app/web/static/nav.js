/* Оболочка: две вкладки («Расписание» открыта по умолчанию, «Журнал» только для старосты) и
   плавающая навигация внизу. Журнал не загружается, пока на него не перешли. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;

  var TABS = [['schedule', 'Расписание', 'schedule'], ['journal', 'Журнал', 'journal']];
  // Короткий список не сжимает пилюлю: иначе она дёргается от каждого касания.
  var STEP_PX = 12, MIN_TOP = 48, MIN_RANGE = 160;
  var current = 'schedule', nav = null, lens = null, panes = {}, lastTop = new WeakMap();

  function setCompact(on) {
    if (nav && nav.classList.contains('compact') !== on) nav.classList.toggle('compact', on);
  }

  function show(name) {
    if (name === current && nav.dataset.ready) return;
    current = name;
    Object.keys(panes).forEach(function (key) { panes[key].hidden = key !== name; });
    nav.querySelectorAll('.tab').forEach(function (tab, i) {
      var on = tab.dataset.tab === name;
      tab.classList.toggle('on', on);
      if (on) { tab.setAttribute('aria-current', 'page'); nav.style.setProperty('--i', i); }
      else tab.removeAttribute('aria-current');
    });
    setCompact(false);
    if (nav.dataset.ready) {
      // Перезапуск анимации «капли»: без чтения раскладки браузер склеит снятие и установку класса.
      lens.classList.remove('jelly');
      void lens.offsetWidth;
      lens.classList.add('jelly');
      Zo.haptic('select');
    }
    Zo.app.setActive(name === 'journal');
    nav.dataset.ready = '1';
  }

  /** Сжимаем пилюлю только от прокрутки расписания: журнал её не прокручивает под собой. */
  function onScroll(event) {
    var el = event.target;
    if (!el || !el.closest || !el.closest('#pane-schedule')) return;
    var range = el.scrollHeight - el.clientHeight;
    if (range < MIN_RANGE) { setCompact(false); return; }
    // Отскок iOS за края уводит scrollTop за границы; без зажима отскок внизу читался как «вверх».
    var now = Math.max(0, Math.min(el.scrollTop, range)), was = lastTop.get(el) || 0;
    if (Math.abs(now - was) < STEP_PX) return;
    lastTop.set(el, now);
    setCompact(now > was && now > MIN_TOP);
  }

  function build() {
    lens = h('span', { class: 'lens', 'aria-hidden': 'true' });
    nav = h('nav', { class: 'nav', 'aria-label': 'Разделы', style: '--n:' + TABS.length }, lens, TABS.map(function (t) {
      return h('button', { class: 'tab' + (t[0] === current ? ' on' : ''), 'data-tab': t[0], onclick: function () { show(t[0]); } },
        Zo.line(t[2]), h('span', null, t[1]));
    }));
    document.body.append(nav);
    nav.addEventListener('click', function () { setCompact(false); }, true);
    document.addEventListener('scroll', onScroll, true);
  }

  /** Вкладка «Журнал» есть только у старосты; остальным навигации нет, переключаться не на что. */
  function headman(flag) {
    document.body.classList.toggle('has-nav', flag);
    if (flag && !nav) build();
    if (!flag && nav) { show('schedule'); nav.remove(); nav = null; }
  }

  function boot() {
    panes.schedule = document.getElementById('pane-schedule');
    panes.journal = document.getElementById('pane-journal');
    Zo.schedule.boot(panes.schedule);
  }

  Zo.shell = { headman: headman, show: show };
  boot();
})();
