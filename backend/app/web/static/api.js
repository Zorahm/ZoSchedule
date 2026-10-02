/* Разговор с сервером. Подпись Telegram (initData) едет в каждом запросе:
   по ней сервер узнаёт старосту, никаких паролей и сессий нет. */
(function () {
  'use strict';
  var Zo = (window.Zo = window.Zo || {});

  function initData() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.initData) return tg.initData;
    // Запасной путь для проверки без Telegram: подпись в адресе, как её кладёт сам Telegram.
    return new URLSearchParams(location.hash.slice(1)).get('tgWebAppData') || '';
  }

  function ApiError(status, code, message) {
    this.status = status; this.code = code; this.message = message;
  }
  Zo.ApiError = ApiError;

  async function call(method, path, body) {
    var response;
    try {
      response = await fetch(path, {
        method: method,
        headers: { Authorization: 'tma ' + initData(), 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store',
      });
    } catch (e) {
      throw new ApiError(0, 'network', 'Нет связи с сервером');
    }
    var data = null;
    try { data = await response.json(); } catch (e) { /* пустое тело */ }
    if (!response.ok) {
      throw new ApiError(response.status, data && data.error || 'error', data && data.message || 'Ошибка ' + response.status);
    }
    return data;
  }

  Zo.api = {
    journal: function (day, week) {
      var q = new URLSearchParams();
      if (day) q.set('day', day);
      if (week) q.set('week', week);
      return call('GET', '/api/journal?' + q.toString());
    },
    marks: function (day, changes) { return call('POST', '/api/marks', { date: day, changes: changes }); },
    sendReport: function (day, options) { return call('POST', '/api/report', { date: day, titles: !options || options.titles !== false }); },
    roster: function () { return call('GET', '/api/roster'); },
    saveRoster: function (students) { return call('PUT', '/api/roster', { students: students }); },
  };
})();
