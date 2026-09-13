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

        function bindAnneeSync(debutId, finId) {
            var debut = document.getElementById(debutId);
            var fin = document.getElementById(finId);
            if (!debut || !fin) return;

            var lastSuggested = '';

            function sync() {
                var val = (debut.value || '').trim();
                if (/^\d{2}$/.test(val)) {
                    var next = (parseInt(val, 10) + 1) % 100;
                    var suggested = next.toString().padStart(2, '0');
                    if (!fin.value || fin.value === lastSuggested) {
                        fin.value = suggested;
                    }
                    lastSuggested = suggested;
                } else if (fin.value === lastSuggested) {
                    fin.value = '';
                }
            }

            debut.addEventListener('input', sync);
            sync();
        }

        var newDebutInput = document.getElementById('annee_debut_court');
        var newFinInput = document.getElementById('annee_fin_court');
        if (newDebutInput) newDebutInput.value = '';
        if (newFinInput) newFinInput.value = '';

        bindAnneeSync('annee_debut_court', 'annee_fin_court');
        bindAnneeSync('edit_annee_debut_court', 'edit_annee_fin_court');

        var modalModifier = document.getElementById('modalModifierAnnee');
        if (modalModifier) {
            modalModifier.addEventListener('show.bs.modal', function (event) {
                var button = event.relatedTarget;
                if (!button) return;

                var anneeId = button.getAttribute('data-annee-id') || '';
                var anneeNom = button.getAttribute('data-annee-nom') || '';
                var dateDebut = button.getAttribute('data-date-debut') || '';
                var dateFin = button.getAttribute('data-date-fin') || '';
                var match = anneeNom.match(/^20(\d{2})[-/]20(\d{2})$/);

                var editIdInput = document.getElementById('edit_annee_id');
                var editCourtInput = document.getElementById('edit_annee_debut_court');
                var editFinCourtInput = document.getElementById('edit_annee_fin_court');
                var editDebutInput = document.getElementById('edit_date_debut');
                var editFinInput = document.getElementById('edit_date_fin');

                if (editIdInput) editIdInput.value = anneeId;
                if (editCourtInput) editCourtInput.value = match ? match[1] : '';
                if (editFinCourtInput) editFinCourtInput.value = match ? match[2] : '';
                if (editDebutInput) editDebutInput.value = dateDebut;
                if (editFinInput) editFinInput.value = dateFin;
            });
        }
    });
}());
