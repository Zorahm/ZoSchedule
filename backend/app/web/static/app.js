/* Журнал старосты: загрузка дня, отметки с автосохранением, смена дня сама. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h, tg = window.Telegram && window.Telegram.WebApp;

  var POLL_MS = 45000, FLUSH_MS = 350, RETRY_MS = 4000;
  var S = { data: null, view: null, weekOf: null, save: 'saved', mode: 'present', pair: null, queue: new Map(), flushTimer: null, sheet: null };
  var top, main, foot;

  function students() { return S.data.day.students; }
  function byId(id) { return students().find(function (s) { return s.id === id; }); }
  function pairAt(slot) { return S.data.day.pairs.find(function (p) { return p.slot === slot; }); }
  function editable() { return S.data && S.data.day.state === 'open' && students().length > 0; }

  /* ---------- отрисовка ---------- */

  function nowSlot() {
    var day = S.data.day;
    if (day.date !== Zo.today()) return null;
    var p = Zo.chrome.progress(day).now;
    return p ? p.slot : null;
  }

  function drawChrome() {
    var data = S.data, on = {
      roster: openRoster, back: function () { go(null); }, pick: go,
      week: function (step) { S.weekOf = Zo.addDays(data.week[0].date, step); load(); },
    };
    top.replaceChildren(Zo.chrome.header(data, S.save, on));
    foot.replaceChildren();
    if (!editable()) return;
    var day = data.day, pair = S.pair && pairAt(S.pair), all = Zo.table.stats(day), stats = all;
    if (pair) stats = { total: day.students.length, marked: all.slots[pair.slot].marked, absent: all.slots[pair.slot].absent };
    if (pair) foot.append(Zo.chrome.scope(pair, { open: function () { openPair(pair.slot); }, clear: function () { togglePair(pair.slot); } }));
    foot.append(Zo.chrome.bar(stats, S.mode, { scoped: !!pair, fill: fillRest,
      mode: function (m) { S.mode = m; Zo.haptic('select'); drawChrome(); } }));
  }

  function drawBody() {
    var day = S.data.day;
    if (!editable()) {
      main.replaceChildren(Zo.chrome.placeholder(S.data, { roster: openRoster }));
      return;
    }
    var sheet = Zo.table.build(day, nowSlot());
    Zo.table.bind(sheet, { tap: tapCell, clear: function (sid, slot) { applyOne(sid, slot, null); Zo.haptic('warning'); },
      pair: togglePair, student: openStudent });
    Zo.table.select(sheet, S.pair);
    main.replaceChildren(sheet);
  }

  function render() { drawChrome(); drawBody(); syncBackButton(); }

  /** После правки меняются только цифры: таблицу и её прокрутку не трогаем. */
  function recount() {
    var sheet = main.querySelector('.sheet');
    var all = sheet ? Zo.table.refresh(sheet, S.data.day) : Zo.table.stats(S.data.day);
    var entry = S.data.week.find(function (e) { return e.date === S.data.day.date; });
    if (entry) { entry.marked = all.marked; entry.total = all.total; }
    drawChrome();
  }

  /* ---------- загрузка ---------- */

  async function load(quiet) {
    try {
      var data = await Zo.api.journal(S.view, S.weekOf);
      var changed = S.data && !S.view && data.current_day !== S.data.current_day;
      Zo.syncClock(data.now);
      var scroll = main.querySelector('.sheet');
      var pos = scroll && !changed && data.day.date === (S.data && S.data.day.date) ? [scroll.scrollTop, scroll.scrollLeft] : null;
      S.data = data;
      if (S.pair && !pairAt(S.pair)) S.pair = null;
      S.weekOf = data.week[0].date;
      mergeQueued();
      render();
      if (pos) { var next = main.querySelector('.sheet'); if (next) { next.scrollTop = pos[0]; next.scrollLeft = pos[1]; } }
      if (changed) Zo.sheets.toast('Открылся новый день: ' + Zo.weekday(data.current_day));
      if (S.save === 'offline' && !S.queue.size) setSave('saved');
    } catch (e) {
      if (quiet && S.data) return;
      main.replaceChildren(Zo.chrome.failure(e, function () { load(); }));
      top.replaceChildren();
      foot.replaceChildren();
    }
  }

  /** Ответ сервера не должен стирать то, что ещё не отправлено. */
  function mergeQueued() {
    S.queue.forEach(function (entry) {
      if (entry.date !== S.data.day.date) return;
      var student = byId(entry.sid);
      if (student) { if (entry.mark) student.marks[entry.slot] = entry.mark; else delete student.marks[entry.slot]; }
    });
  }

  function go(day) { S.pair = null; S.view = day; if (day) S.weekOf = null; load(); }

  /* ---------- отметки ---------- */

  function setSave(state) { S.save = state; var el = top.querySelector('.save'); if (el) drawChrome(); }

  function put(student, slot, mark) {
    if (mark) student.marks[slot] = mark; else delete student.marks[slot];
    var pair = pairAt(slot), sheet = main.querySelector('.sheet');
    if (sheet && pair) Zo.table.paint(sheet, student, pair, mark);
    S.queue.set(S.data.day.date + '|' + student.id + '|' + slot, { date: S.data.day.date, sid: student.id, slot: slot, mark: mark });
  }

  function applyOne(sid, slot, mark) {
    var student = byId(sid);
    if (!student) return;
    put(student, slot, mark);
    afterEdit();
  }

  function afterEdit() { recount(); scheduleFlush(FLUSH_MS); }

  function tapCell(sid, slot) {
    var student = byId(sid), current = student.marks[slot];
    Zo.haptic('select');
    applyOne(sid, slot, current === 'present' ? 'absent' : 'present');
  }

  /** Массовая правка с возможностью откатить: меняет ячейки и запоминает, что было. */
  function bulk(cells, mark, text) {
    var before = [];
    cells.forEach(function (c) {
      var was = c.student.marks[c.slot] || null;
      if (was !== mark) { before.push({ student: c.student, slot: c.slot, mark: was }); put(c.student, c.slot, mark); }
    });
    if (!before.length) { Zo.sheets.toast('Менять нечего'); return; }
    afterEdit();
    Zo.haptic('success');
    Zo.sheets.toast(text(before.length), function () {
      before.forEach(function (b) { put(b.student, b.slot, b.mark); });
      afterEdit();
    });
  }

  function togglePair(slot) {
    S.pair = S.pair === slot ? null : slot;
    Zo.haptic('select');
    var sheet = main.querySelector('.sheet');
    if (sheet) Zo.table.select(sheet, S.pair);
    drawChrome();
  }

  function fillRest() {
    var cells = [], pairs = S.data.day.pairs.filter(function (p) { return !S.pair || p.slot === S.pair; });
    students().forEach(function (s) {
      pairs.forEach(function (p) { if (!s.marks[p.slot]) cells.push({ student: s, slot: p.slot }); });
    });
    var letter = S.mode === 'absent' ? 'Н' : 'П', where = S.pair ? ' · ' + pairAt(S.pair).number + ' пара' : '';
    bulk(cells, S.mode, function (n) { return 'Поставил ' + letter + ' в ' + n + ' ' + Zo.plural(n, 'ячейку', 'ячейки', 'ячеек') + where; });
  }

  function openPair(slot) {
    var pair = pairAt(slot), all = Zo.table.stats(S.data.day);
    Zo.sheets.pair(pair, all.slots[slot], students().length, function (mark) {
      var cells = students().map(function (s) { return { student: s, slot: slot }; });
      bulk(cells, mark, function (n) { return (mark === 'present' ? 'П' : mark === 'absent' ? 'Н' : 'Сброс') + ' в ' + n + ' ' + Zo.plural(n, 'ячейке', 'ячейках', 'ячейках') + ' · ' + pair.number + ' пара'; });
    });
  }

  function openStudent(sid) {
    var student = byId(sid), pairs = S.data.day.pairs;
    var marked = pairs.filter(function (p) { return student.marks[p.slot]; }).length;
    var absent = pairs.filter(function (p) { return student.marks[p.slot] === 'absent'; }).length;
    Zo.sheets.student(student, pairs.length, marked, absent, function (mark) {
      var cells = pairs.map(function (p) { return { student: student, slot: p.slot }; });
      bulk(cells, mark, function () { return Zo.shortName(student.name) + (mark === 'absent' ? ': Н весь день' : mark ? ': П весь день' : ': сброшено'); });
    });
  }

  /* ---------- отправка ---------- */

  function scheduleFlush(ms) { clearTimeout(S.flushTimer); S.flushTimer = setTimeout(flush, ms); setSave('saving'); }

  async function flush() {
    if (!S.queue.size) { setSave('saved'); return; }
    var sent = Array.from(S.queue.entries()), byDate = {};
    sent.forEach(function (pair) { (byDate[pair[1].date] = byDate[pair[1].date] || []).push(pair[1]); });
    try {
      for (var date in byDate) {
        await Zo.api.marks(date, byDate[date].map(function (c) { return { student_id: c.sid, slot: c.slot, mark: c.mark }; }));
      }
      sent.forEach(function (pair) { if (S.queue.get(pair[0]) === pair[1]) S.queue.delete(pair[0]); });
      if (S.queue.size) scheduleFlush(FLUSH_MS); else setSave('saved');
    } catch (e) {
      if (e.status === 403 || e.status === 404) {
        S.queue.clear(); setSave('saved');
        Zo.sheets.toast(e.message); load(true);
      } else { setSave('offline'); clearTimeout(S.flushTimer); S.flushTimer = setTimeout(flush, RETRY_MS); }
    }
  }

  /* ---------- список группы ---------- */

  async function openRoster() {
    try {
      var roster = await Zo.api.roster();
      Zo.roster.open(roster.students, async function (list) {
        await Zo.api.saveRoster(list);
        await load(true);
      }, syncBackButton);
      syncBackButton();
    } catch (e) { Zo.sheets.toast(e.message); }
  }

  /* ---------- Telegram ---------- */

  function setTheme() {
    if (!tg) return;
    document.documentElement.dataset.theme = tg.colorScheme === 'dark' ? 'dark' : 'light';
    var bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); if (tg.setBottomBarColor) tg.setBottomBarColor(bg); } catch (e) { /* старый клиент */ }
  }

  function syncBackButton() {
    if (!tg || !tg.BackButton) return;
    var overlay = Zo.roster.isOpen() || Zo.sheets.isOpen();
    if (overlay) tg.BackButton.show(); else tg.BackButton.hide();
  }

  function boot() {
    var app = document.getElementById('app');
    top = h('div', { class: 'slot-top' }); main = h('main', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' }); foot = h('div');
    app.append(top, main, foot);
    if (tg) {
      tg.ready(); tg.expand();
      try { tg.disableVerticalSwipes(); } catch (e) { /* не везде есть */ }
      tg.onEvent('themeChanged', setTheme);
      tg.BackButton.onClick(function () { if (Zo.roster.isOpen()) document.querySelector('.screen .icon-btn').click(); else Zo.sheets.close(); });
    }
    setTheme();
    Zo.sheets.onClose = syncBackButton;
    load();
    setInterval(function () { if (!S.queue.size && !document.hidden && !Zo.roster.isOpen()) load(true); }, POLL_MS);
    setInterval(function () { if (S.data && !document.hidden) { drawChrome(); } }, 30000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) load(true); });
  }

  Zo.app = { boot: boot, state: S };
  boot();
})();
