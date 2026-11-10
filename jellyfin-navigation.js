/* Keep the Remuxify plugin shortcut inside Jellyfin's current web client. */
(function () {
  'use strict';
  document.addEventListener('click', function (event) {
    const anchor = event.target.closest && event.target.closest('a[href]');
    if (anchor && anchor.getAttribute('href') === '#/configurationpage?name=Remuxify') {
      anchor.setAttribute('target', '_self');
    }
  }, true);
}());
