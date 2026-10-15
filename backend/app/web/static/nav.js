/* Оболочка: две вкладки («Расписание» открыта по умолчанию, «Журнал» только для старосты) и
   плавающая навигация внизу. Журнал не загружается, пока на него не перешли. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;

  var TABS = [['schedule', 'Расписание', 'schedule'], ['journal', 'Журнал', 'journal']];
  var current = 'schedule', nav = null, lens = null, panes = {}, lastTop = new WeakMap();

  function placeLens() {
    var on = nav && nav.querySelector('.tab.on');
    if (!on) return;
    nav.style.setProperty('--lx', on.offsetLeft + 'px');
    nav.style.setProperty('--lw', on.offsetWidth + 'px');
  }

  function show(name) {
    if (name === current && nav.dataset.ready) return;
    current = name;
    Object.keys(panes).forEach(function (key) { panes[key].hidden = key !== name; });
    nav.querySelectorAll('.tab').forEach(function (tab) {
      var on = tab.dataset.tab === name;
      tab.classList.toggle('on', on);
      if (on) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
    });
    nav.classList.remove('compact');
    placeLens();
    // Лупа переезжает вместе с изменением ширины вкладок, поэтому ещё раз после перехода.
    setTimeout(placeLens, 300);
    Zo.app.setActive(name === 'journal');
    if (nav.dataset.ready) Zo.haptic('select');
    nav.dataset.ready = '1';
  }

  function build() {
    lens = h('span', { class: 'lens', 'aria-hidden': 'true' });
    nav = h('nav', { class: 'nav', 'aria-label': 'Разделы' }, lens, TABS.map(function (t) {
      return h('button', { class: 'tab' + (t[0] === current ? ' on' : ''), 'data-tab': t[0], onclick: function () { show(t[0]); } },
        Zo.line(t[2]), h('span', null, t[1]));
    }));
    document.body.append(nav);
    placeLens();
    window.addEventListener('resize', placeLens);
    nav.addEventListener('click', function () { nav.classList.remove('compact'); setTimeout(placeLens, 300); }, true);
    // Прокрутка вниз сжимает пилюлю до вкладки, на которой стоим; вверх возвращает.
    document.addEventListener('scroll', function (event) {
      var el = event.target;
      if (!el || typeof el.scrollTop !== 'number') return;
      var was = lastTop.get(el) || 0, now = el.scrollTop;
      if (Math.abs(now - was) < 10) return;
      lastTop.set(el, now);
      var compact = now > was && now > 40;
      if (nav.classList.contains('compact') !== compact) { nav.classList.toggle('compact', compact); setTimeout(placeLens, 300); }
    }, true);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeLens);
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
