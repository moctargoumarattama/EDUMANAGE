(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.js-confirm-form').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                if (!window.confirm(form.dataset.confirm || 'Confirmer cette action ?')) {
                    event.preventDefault();
                }
            });
        });

        document.querySelectorAll('.js-confirm-link').forEach(function (link) {
            link.addEventListener('click', function (event) {
                if (!window.confirm(link.dataset.confirm || 'Confirmer cette action ?')) {
                    event.preventDefault();
                }
            });
        });
    });
}());
