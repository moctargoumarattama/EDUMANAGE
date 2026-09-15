(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        // Confirmations
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

        // 1. Auto-remplissage dynamique lors de la création d'année
        var newDebutDate = document.querySelector('form[action*="gestion_annees"] input[name="date_debut"]:not(#edit_date_debut)');
        var newFinDate = document.querySelector('form[action*="gestion_annees"] input[name="date_fin"]:not(#edit_date_fin)');
        var newAnneeInput = document.getElementById('annee_scolaire');

        if (newDebutDate) {
            newDebutDate.addEventListener('change', function () {
                var val = this.value; // YYYY-MM-DD
                if (val && val.length >= 4) {
                    var startYear = parseInt(val.substring(0, 4), 10);
                    if (!isNaN(startYear)) {
                        var endYear = startYear + 1;
                        if (newAnneeInput && !newAnneeInput.value.trim()) {
                            newAnneeInput.value = startYear + '-' + endYear;
                        }
                        if (newFinDate && !newFinDate.value) {
                            newFinDate.value = endYear + '-06-30';
                        }
                    }
                }
            });
        }

        if (newAnneeInput) {
            newAnneeInput.addEventListener('input', function () {
                var val = this.value.trim();
                var match = val.match(/^(20\d{2})[-/](20\d{2})$/) || val.match(/^(\d{2})$/);
                if (match) {
                    var startYear = match[1].length === 2 ? 2000 + parseInt(match[1], 10) : parseInt(match[1], 10);
                    var endYear = startYear + 1;
                    if (newDebutDate && !newDebutDate.value) {
                        newDebutDate.value = startYear + '-09-01';
                    }
                    if (newFinDate && !newFinDate.value) {
                        newFinDate.value = endYear + '-06-30';
                    }
                }
            });
        }

        // 2. Mise à jour EN DIRECT du calendrier des semestres dans la modal
        function formatDateFR(dateObj) {
            if (!dateObj || isNaN(dateObj.getTime())) return '';
            var day = String(dateObj.getDate()).padStart(2, '0');
            var month = String(dateObj.getMonth() + 1).padStart(2, '0');
            var year = dateObj.getFullYear();
            return day + '/' + month + '/' + year;
        }

        function updateSemestresPreview(modal) {
            if (!modal) return;
            var inputFinS1 = modal.querySelector('.input-fin-s1');
            var previewS1Fin = modal.querySelector('.preview-s1-fin');
            var previewS2Debut = modal.querySelector('.preview-s2-debut');

            if (!inputFinS1 || !inputFinS1.value) return;

            // Date sélectionnée (ex: 2027-02-18)
            var parts = inputFinS1.value.split('-');
            if (parts.length !== 3) return;

            var finS1Date = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
            if (isNaN(finS1Date.getTime())) return;

            // Semestre 1 Fin = Date sélectionnée
            if (previewS1Fin) {
                previewS1Fin.textContent = formatDateFR(finS1Date);
            }

            // Semestre 2 Début = Fin S1 + 1 jour
            var debutS2Date = new Date(finS1Date.getTime());
            debutS2Date.setDate(debutS2Date.getDate() + 1);

            if (previewS2Debut) {
                previewS2Debut.textContent = formatDateFR(debutS2Date);
            }
        }

        // Écouter les évènements sur chaque modal de semestres
        document.querySelectorAll('.modal-semestres').forEach(function (modal) {
            var inputFinS1 = modal.querySelector('.input-fin-s1');
            if (inputFinS1) {
                inputFinS1.addEventListener('input', function () {
                    updateSemestresPreview(modal);
                });
                inputFinS1.addEventListener('change', function () {
                    updateSemestresPreview(modal);
                });
            }

            modal.addEventListener('show.bs.modal', function () {
                updateSemestresPreview(modal);
            });
        });

        // 3. Modal Modifier Année
        var editDebutDate = document.getElementById('edit_date_debut');
        var editAnneeInput = document.getElementById('edit_annee_scolaire');
        var editFinDate = document.getElementById('edit_date_fin');

        if (editDebutDate) {
            editDebutDate.addEventListener('change', function () {
                var val = this.value;
                if (val && val.length >= 4) {
                    var startYear = parseInt(val.substring(0, 4), 10);
                    if (!isNaN(startYear)) {
                        var endYear = startYear + 1;
                        if (editAnneeInput) {
                            editAnneeInput.value = startYear + '-' + endYear;
                        }
                        if (editFinDate && !editFinDate.value) {
                            editFinDate.value = endYear + '-06-30';
                        }
                    }
                }
            });
        }

        var modalModifier = document.getElementById('modalModifierAnnee');
        if (modalModifier) {
            modalModifier.addEventListener('show.bs.modal', function (event) {
                var button = event.relatedTarget;
                if (!button) return;

                var anneeId = button.getAttribute('data-annee-id') || '';
                var anneeNom = button.getAttribute('data-annee-nom') || '';
                var dateDebut = button.getAttribute('data-date-debut') || '';
                var dateFin = button.getAttribute('data-date-fin') || '';

                var editIdInput = document.getElementById('edit_annee_id');
                var editAnneeInput = document.getElementById('edit_annee_scolaire');
                var editDebutInput = document.getElementById('edit_date_debut');
                var editFinInput = document.getElementById('edit_date_fin');

                if (editIdInput) editIdInput.value = anneeId;
                if (editAnneeInput) editAnneeInput.value = anneeNom;
                if (editDebutInput) editDebutInput.value = dateDebut;
                if (editFinInput) editFinInput.value = dateFin;
            });
        }
    });
}());
