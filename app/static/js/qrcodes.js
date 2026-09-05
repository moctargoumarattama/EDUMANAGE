(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.js-print-page').forEach(function (button) {
            button.addEventListener('click', function () {
                window.print();
            });
        });
    });
}());
