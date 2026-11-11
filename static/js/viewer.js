/* Учёт времени просмотра: раз в 10 секунд сообщает серверу, какие страницы сейчас на экране.
   Передаётся только номер страницы; содержимое документов и значения форм не читаются и нигде не сохраняются.
   Без JavaScript просмотр работает как обычно, только время не учитывается. */
(function () {
  'use strict';
  var box = document.querySelector('[data-pulse-url]');
  if (!box || !window.IntersectionObserver || !window.fetch) return;
  var url = box.getAttribute('data-pulse-url');
  var token = box.getAttribute('data-csrf');
  var visible = {};

  function send(pages) {
    if (!pages.length) return;
    var body = new URLSearchParams();
    pages.forEach(function (page) { body.append('page', page); });
    fetch(url, {
      method: 'POST', credentials: 'same-origin', keepalive: true, body: body,
      headers: { 'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded' }
    }).catch(function () {});
  }

  // Страницы, появившиеся в одном тике, уходят одним запросом.
  var pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    setTimeout(function () { pending = false; send(Object.keys(visible)); }, 300);
  }

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var page = entry.target.getAttribute('data-page');
      if (entry.isIntersecting) {
        if (!visible[page]) { visible[page] = true; schedule(); }
      } else {
        delete visible[page];
      }
    });
  }, { threshold: 0.4 });

  box.querySelectorAll('img[data-page]').forEach(function (img) { observer.observe(img); });

  setInterval(function () {
    if (document.visibilityState !== 'visible') return;
    send(Object.keys(visible));
  }, 10000);
})();
