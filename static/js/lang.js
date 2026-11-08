/* Переключение языка без перезагрузки заполненной формы.
   Значения полей (включая пароль и одноразовый код) остаются только в памяти страницы:
   они не пишутся в URL, localStorage, sessionStorage и журнал. Без JavaScript работает обычная форма с кнопкой. */
(function () {
  'use strict';
  document.addEventListener('change', function (event) {
    var select = event.target;
    if (!select || select.id !== 'language' || !select.form || select.form.id !== 'language-form') return;
    event.preventDefault();
    switchLanguage(select.form);
  });

  function snapshot(root) {
    var map = [];
    root.querySelectorAll('input, select, textarea').forEach(function (el) {
      if (!el.name || el.type === 'hidden' || el.type === 'file' || el.name === 'csrfmiddlewaretoken') return;
      map.push({ name: el.name, type: el.type, value: el.value, checked: el.checked, index: el.selectedIndex });
    });
    return map;
  }

  function restore(root, saved) {
    var used = {};
    saved.forEach(function (item) {
      var candidates = root.querySelectorAll('[name="' + item.name + '"]');
      var target = candidates[used[item.name] || 0];
      used[item.name] = (used[item.name] || 0) + 1;
      if (!target) return;
      if (target.type === 'checkbox' || target.type === 'radio') target.checked = item.checked;
      else if (target.tagName === 'SELECT') target.selectedIndex = item.index;
      else target.value = item.value;
    });
  }

  function switchLanguage(form) {
    var main = document.getElementById('content');
    var saved = snapshot(main);
    var body = new FormData(form);
    fetch(form.action, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' }
    }).then(function (response) {
      if (!response.ok) throw new Error('language');
      return fetch(window.location.href, { credentials: 'same-origin', cache: 'no-store' });
    }).then(function (response) {
      return response.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var newMain = doc.getElementById('content');
      var newHeader = doc.getElementById('site-header');
      if (!newMain || !newHeader) throw new Error('language');
      document.getElementById('content').replaceWith(document.importNode(newMain, true));
      document.getElementById('site-header').replaceWith(document.importNode(newHeader, true));
      restore(document.getElementById('content'), saved);
      document.documentElement.lang = doc.documentElement.lang;
      document.title = doc.title;
      var sel = document.getElementById('language');
      if (sel) sel.focus();
    }).catch(function () {
      form.submit(); // запасной путь: обычная отправка формы
    });
  }
})();
