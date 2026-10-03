/* Экран «Список группы»: ФИО правятся прямо в строках, новых можно добавить пачкой. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});
  var h = Zo.h;
  var screen = null;

  function collapse(text) { return text.replace(/\s+/g, ' ').trim(); }

  /** Строки из вставленного текста: «1. Иванов Иван» и «Иванов Иван,» тоже годятся. */
  function parseLines(text) {
    return text.split(/\r?\n/).map(function (line) {
      return collapse(line.replace(/^\s*\d+[.)]\s*/, '').replace(/[,;]+$/, ''));
    }).filter(Boolean);
  }

  /**
   * rows: [{id, name}], onSave(list) -> Promise. Закрытие без сохранения ничего не меняет.
   */
  function open(rows, onSave, onClose) {
    var draft = rows.map(function (row) { return { id: row.id, name: row.name }; });
    var list = h('div', { class: 'roster-list', role: 'list' });
    var count = h('span', { class: 'count' });
    var error = h('div', { class: 'err', role: 'alert' });
    var save = h('button', { class: 'btn', onclick: submit }, 'Сохранить');
    var bulkBox = null;

    function close() { if (screen) screen.remove(); screen = null; if (onClose) onClose(); }

    function refreshState() {
      var filled = draft.filter(function (r) { return collapse(r.name); }).length;
      count.textContent = filled + ' ' + Zo.plural(filled, 'человек', 'человека', 'человек');
      save.disabled = false;
    }

    function drawRows(focusLast) {
      list.replaceChildren.apply(list, draft.map(function (row, index) {
        var input = h('input', { type: 'text', value: row.name, placeholder: 'Фамилия Имя Отчество', autocomplete: 'off',
          enterkeyhint: 'next', 'aria-label': 'ФИО, строка ' + (index + 1) });
        input.addEventListener('input', function () { row.name = input.value; refreshState(); });
        input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addRow(); } });
        return h('div', { class: 'rrow', role: 'listitem' }, h('i', null, index + 1), input,
          h('button', { class: 'del', 'aria-label': 'Убрать из списка', onclick: function () { draft.splice(index, 1); drawRows(); refreshState(); } }, Zo.icon('x')));
      }));
      refreshState();
      if (focusLast) {
        var inputs = list.querySelectorAll('input');
        if (inputs.length) inputs[inputs.length - 1].focus();
      }
    }

    function addRow() { draft.push({ id: null, name: '' }); drawRows(true); list.scrollTop = list.scrollHeight; }

    function showBulk() {
      if (bulkBox) return;
      var area = h('textarea', { id: 'bulk-text', placeholder: 'Абрамов Илья Сергеевич\nБаранова Алина Игоревна\n…', spellcheck: 'false' });
      bulkBox = h('div', { class: 'bulk' },
        h('label', { for: 'bulk-text' }, 'Вставьте список, по одному ФИО в строке'),
        area,
        h('div', { class: 'row' },
          h('button', { class: 'btn ghost', onclick: hideBulk }, 'Отмена'),
          h('button', { class: 'btn', onclick: function () {
            var known = {};
            draft.forEach(function (r) { known[collapse(r.name).toLowerCase()] = true; });
            parseLines(area.value).forEach(function (name) {
              if (!known[name.toLowerCase()]) { draft.push({ id: null, name: name }); known[name.toLowerCase()] = true; }
            });
            hideBulk(); drawRows();
            list.scrollTop = list.scrollHeight;
          } }, 'Добавить в список')));
      screen.insertBefore(bulkBox, list);
      list.hidden = true;
      area.focus();
    }
    function hideBulk() { if (bulkBox) bulkBox.remove(); bulkBox = null; list.hidden = false; }

    async function submit() {
      var seen = {}, clean = [];
      for (var i = 0; i < draft.length; i++) {
        var name = collapse(draft[i].name);
        if (!name) continue;
        if (seen[name.toLowerCase()]) { error.textContent = 'Дважды в списке: ' + name; return; }
        seen[name.toLowerCase()] = true;
        clean.push({ id: draft[i].id, name: name });
      }
      error.textContent = '';
      save.disabled = true; save.textContent = 'Сохраняю…';
      try {
        await onSave(clean);
        Zo.haptic('success');
        close();
      } catch (e) {
        error.textContent = e.message || 'Не удалось сохранить';
        save.disabled = false; save.textContent = 'Сохранить';
      }
    }

    screen = h('div', { class: 'screen', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Список группы' },
      h('div', { class: 'screen-top' },
        h('button', { class: 'icon-btn', 'aria-label': 'Назад', onclick: close }, Zo.icon('left')),
        h('h2', null, 'Список группы'), count),
      list,
      h('div', { class: 'row-tools' },
        h('button', { class: 'btn ghost', onclick: addRow }, Zo.icon('plus'), 'Добавить'),
        h('button', { class: 'btn ghost', onclick: showBulk }, 'Вставить списком')),
      error,
      h('div', { class: 'bar-b' }, save));
    document.body.append(screen);
    drawRows();
    if (!draft.length) showBulk();
    return { close: close };
  }

  Zo.roster = { open: open, isOpen: function () { return !!screen; } };
})();
