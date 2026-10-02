/* Подставной сервер для просмотра дизайна без бэкенда: подключается вместо api.js.
   Часы стоят на пятнице 2 октября 2026, 11:20 (идёт вторая пара) и дальше идут сами.
   Отметки живут в памяти страницы. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});

  var BASE = { date: '2026-10-02', minutes: 11 * 60 + 20, at: Date.now() };
  function nowIso() {
    var total = BASE.minutes + Math.floor((Date.now() - BASE.at) / 60000);
    var hh = String(Math.floor(total / 60)).padStart(2, '0'), mm = String(total % 60).padStart(2, '0');
    return BASE.date + 'T' + hh + ':' + mm + ':00+03:00';
  }

  var TIMES = [['08:30', '10:00'], ['10:10', '11:40'], ['12:10', '13:40'], ['13:50', '15:20']];
  var SUBJECTS = {
    math: ['Математический анализ', 'лекция', '210', 'Орлов П. П.'],
    web: ['Веб-программирование', 'практическое занятие', '305', 'Петров А. А.'],
    db: ['Базы данных', 'лабораторный практикум', '118', 'Смирнов А. В.'],
    os: ['Операционные системы', 'лекция', '305', null],
    lang: ['Иностранный язык в профессиональной деятельности', 'практическое занятие', '402', 'Сидорова Н. В.'],
    sport: ['Физкультура', 'практическое занятие', 'Зал', 'Кузнецов А. А.'],
    design: ['Проектирование и дизайн информационных систем', 'лекция', '214', 'Иванов И. И.'],
  };
  var WEEK = [['math', 'web', 'sport'], ['db', 'lang', 'math', 'os'], ['design', 'web', 'db', 'lang'], [], ['math', 'web', 'os', 'lang'], ['design', 'lang']];
  var PUBLISHED_TO = '2026-10-10';

  var NAMES = ['Абрамов Илья Сергеевич', 'Баранова Алина Игоревна', 'Васильев Артём Олегович', 'Громова Дарья Андреевна', 'Дроздов Кирилл Павлович',
    'Егорова Полина Артёмовна', 'Жуков Максим Денисович', 'Зайцева Мария Ильинична', 'Иванов Никита Алексеевич', 'Исаева Ксения Романовна',
    'Козлов Егор Викторович', 'Лебедева София Дмитриевна', 'Макаров Тимур Русланович', 'Новикова Анастасия Павловна', 'Орлов Матвей Степанович',
    'Панина Вероника Сергеевна', 'Романов Данила Евгеньевич', 'Сидоров Артур Николаевич', 'Тихонова Елизавета Максимовна', 'Устинов Глеб Аркадьевич',
    'Фомина Алиса Валерьевна', 'Харитонов Лев Борисович', 'Шевцова Дарина Олеговна', 'Яковлев Руслан Маратович'];
  var roster = NAMES.map(function (name, i) { return { id: i + 1, name: name }; });
  var nextId = roster.length + 1;
  var marks = {}; // date -> "sid|slot" -> mark

  function pairsOf(date) {
    var dow = (Zo.parseDay(date).getDay() + 6) % 7;
    return (WEEK[dow] || []).map(function (key, i) {
      var s = SUBJECTS[key];
      return { slot: TIMES[i][0], number: i + 1, start: TIMES[i][0], end: TIMES[i][1], title: s[0], kind: s[1], room: s[2], teacher: s[3], lessons: 1 };
    });
  }

  function stateOf(date, pairs) {
    if (date > PUBLISHED_TO) return 'unpublished';
    if (!pairs.length) return 'off';
    var now = nowIso();
    var opensAt = date + 'T' + pairs[0].start + ':00+03:00';
    return opensAt <= now ? 'open' : 'locked';
  }

  /* Прошлые дни заполнены почти целиком, чтобы полоса недели выглядела живой. */
  function seed(date, pairs) {
    if (marks[date]) return marks[date];
    var cells = (marks[date] = {});
    var fill = date < '2026-09-30' ? 1 : date === '2026-09-30' ? 0.82 : date === BASE.date ? 0.3 : 0;
    var n = 0;
    pairs.forEach(function (pair, pi) {
      roster.forEach(function (student, si) {
        n = (si * 7 + pi * 13 + date.charCodeAt(9) * 3) % 23;
        var limit = date === BASE.date ? (pi === 0 ? 24 : pi === 1 ? 14 : 0) : fill * 24;
        if (si < limit) cells[student.id + '|' + pair.slot] = (n === 3 || n === 11 && pi === 0) ? 'absent' : 'present';
      });
    });
    return cells;
  }

  function sortedRoster() {
    return roster.slice().sort(function (a, b) { return a.name.localeCompare(b.name, 'ru'); });
  }

  function dayView(date) {
    var pairs = pairsOf(date), state = stateOf(date, pairs), cells = state === 'open' ? seed(date, pairs) : {};
    return {
      date: date, state: state, pairs: pairs,
      opens_at: state === 'locked' ? date + 'T' + pairs[0].start + ':00+03:00' : null,
      students: state === 'open' ? sortedRoster().map(function (s) {
        var own = {};
        pairs.forEach(function (p) { var m = cells[s.id + '|' + p.slot]; if (m) own[p.slot] = m; });
        return { id: s.id, name: s.name, marks: own };
      }) : [],
    };
  }

  function weekEntry(date) {
    var view = dayView(date), marked = view.students.reduce(function (n, s) { return n + Object.keys(s.marks).length; }, 0);
    return { date: date, state: view.state, marked: marked, total: view.state === 'open' ? view.students.length * view.pairs.length : 0 };
  }

  function delay(value) { return new Promise(function (ok) { setTimeout(function () { ok(value); }, 140); }); }

  Zo.api = {
    journal: function (day, week) {
      var current = BASE.date, shown = day || current;
      var monday = Zo.monday(week || shown);
      var entries = [];
      for (var i = 0; i < 6; i++) entries.push(weekEntry(Zo.addDays(monday, i)));
      return delay({ now: nowIso(), group: 'ОККИПд-307', current_day: current, next_opens_at: '2026-10-03T08:30:00+03:00',
        week: entries, day: dayView(shown) });
    },
    marks: function (date, changes) {
      var cells = seed(date, pairsOf(date));
      changes.forEach(function (c) {
        var key = c.student_id + '|' + c.slot;
        if (c.mark) cells[key] = c.mark; else delete cells[key];
      });
      return delay({ saved: changes.length });
    },
    roster: function () { return delay({ students: sortedRoster() }); },
    saveRoster: function (list) {
      roster = list.map(function (row) { return { id: row.id || nextId++, name: row.name }; });
      return delay({ students: sortedRoster() });
    },
  };
})();
