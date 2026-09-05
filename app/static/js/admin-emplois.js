(function () {
    'use strict';

    function normalize(value) {
        return (value || '').toString().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    }

    function applyFilters() {
        const search = normalize(document.getElementById('scheduleSearch')?.value);
        const day = document.getElementById('scheduleDay')?.value || '';
        const items = document.querySelectorAll('#scheduleTable tbody tr, #scheduleCards .schedule-card');

        items.forEach(function (item) {
            const itemText = normalize(item.dataset.search);
            const itemDay = item.dataset.day || '';
            const matchesSearch = !search || itemText.includes(search);
            const matchesDay = !day || itemDay === day;
            item.classList.toggle('d-none', !(matchesSearch && matchesDay));
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('scheduleSearch')?.addEventListener('input', applyFilters);
        document.getElementById('scheduleDay')?.addEventListener('change', applyFilters);

        document.querySelectorAll('.js-confirm-form').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                const message = form.dataset.confirm || 'Confirmer cette action ?';
                if (!window.confirm(message)) {
                    event.preventDefault();
                }
            });
        });
    });
}());
