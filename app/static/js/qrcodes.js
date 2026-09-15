(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        // 1. Bouton Impression
        document.querySelectorAll('.js-print-page').forEach(function (button) {
            button.addEventListener('click', function () {
                // Avant imprimer, s'assurer que toutes les classes sont dépliées
                expandAllClasses();
                window.print();
            });
        });

        // 2. Éléments du DOM pour le filtrage
        var searchInput = document.getElementById('qrSearchInput');
        var searchClear = document.getElementById('qrSearchClear');
        var filterClasse = document.getElementById('filterClasseSelect');
        var filterStats = document.getElementById('qrFilterStats');
        var btnResetEmpty = document.getElementById('btnResetFiltersEmpty');
        var noResultsState = document.getElementById('noQrFoundState');
        var classListContainer = document.getElementById('classQrcodeList');

        // Bouton Tout déplier / Tout replier
        var btnToggleAll = document.getElementById('btnToggleAllClassQrcodes');
        var iconToggleAll = document.getElementById('iconToggleAll');
        var textToggleAll = document.getElementById('textToggleAll');
        var allExpanded = false;

        var classCards = Array.from(document.querySelectorAll('.class-qrcode-card'));
        var totalClassesCount = classCards.length;

        function normalizeStr(str) {
            if (!str) return '';
            return str.toString().toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '');
        }

        function expandAllClasses() {
            classCards.forEach(function (card) {
                var collapseEl = card.querySelector('.collapse');
                if (collapseEl && !collapseEl.classList.contains('show')) {
                    if (window.bootstrap && window.bootstrap.Collapse) {
                        var bsCollapse = window.bootstrap.Collapse.getOrCreateInstance(collapseEl);
                        bsCollapse.show();
                    } else {
                        collapseEl.classList.add('show');
                    }
                }
            });
            allExpanded = true;
            if (iconToggleAll) iconToggleAll.className = 'fas fa-chevron-up me-1';
            if (textToggleAll) textToggleAll.textContent = 'Tout replier';
        }

        function collapseAllClasses() {
            classCards.forEach(function (card) {
                var collapseEl = card.querySelector('.collapse');
                if (collapseEl && collapseEl.classList.contains('show')) {
                    if (window.bootstrap && window.bootstrap.Collapse) {
                        var bsCollapse = window.bootstrap.Collapse.getOrCreateInstance(collapseEl);
                        bsCollapse.hide();
                    } else {
                        collapseEl.classList.remove('show');
                    }
                }
            });
            allExpanded = false;
            if (iconToggleAll) iconToggleAll.className = 'fas fa-chevron-down me-1';
            if (textToggleAll) textToggleAll.textContent = 'Tout déplier';
        }

        if (btnToggleAll) {
            btnToggleAll.addEventListener('click', function () {
                if (allExpanded) {
                    collapseAllClasses();
                } else {
                    expandAllClasses();
                }
            });
        }

        // Action de filtrage en direct
        function applyFilters() {
            var query = searchInput ? normalizeStr(searchInput.value.trim()) : '';
            var selectedClasse = filterClasse ? normalizeStr(filterClasse.value.trim()) : '';

            if (searchClear) {
                searchClear.classList.toggle('d-none', !query);
            }

            var visibleClassesCount = 0;
            var visibleStudentsCount = 0;

            classCards.forEach(function (card) {
                var classeNom = normalizeStr(card.dataset.nom);
                var elevesStr = normalizeStr(card.dataset.eleves);
                var items = Array.from(card.querySelectorAll('.qr-item-col'));

                var matchClasseFilter = !selectedClasse || classeNom === selectedClasse;
                var matchSearch = !query || classeNom.includes(query) || elevesStr.includes(query);

                if (matchClasseFilter && matchSearch) {
                    card.style.display = '';
                    visibleClassesCount++;

                    // Filtrer les éléments individuels si une recherche est tapée
                    var cardVisibleStudents = 0;
                    items.forEach(function (item) {
                        var eleveNom = normalizeStr(item.dataset.eleve);
                        if (!query || eleveNom.includes(query) || classeNom.includes(query)) {
                            item.style.display = '';
                            cardVisibleStudents++;
                        } else {
                            item.style.display = 'none';
                        }
                    });
                    visibleStudentsCount += cardVisibleStudents;

                    // Si l'utilisateur fait une recherche spécifique, déplier automatiquement la classe
                    if (query && cardVisibleStudents > 0) {
                        var collapseEl = card.querySelector('.collapse');
                        if (collapseEl && !collapseEl.classList.contains('show')) {
                            if (window.bootstrap && window.bootstrap.Collapse) {
                                window.bootstrap.Collapse.getOrCreateInstance(collapseEl).show();
                            } else {
                                collapseEl.classList.add('show');
                            }
                        }
                    }
                } else {
                    card.style.display = 'none';
                }
            });

            if (noResultsState) {
                noResultsState.style.display = (visibleClassesCount === 0 && totalClassesCount > 0) ? 'block' : 'none';
            }
            if (classListContainer) {
                classListContainer.style.display = (visibleClassesCount === 0 && totalClassesCount > 0) ? 'none' : 'block';
            }
            if (filterStats) {
                filterStats.textContent = visibleClassesCount + ' classe(s) affichée(s) — ' + visibleStudentsCount + ' QR code(s)';
            }
        }

        if (searchInput) {
            searchInput.addEventListener('input', applyFilters);
        }
        if (searchClear) {
            searchClear.addEventListener('click', function () {
                searchInput.value = '';
                applyFilters();
                searchInput.focus();
            });
        }
        if (filterClasse) {
            filterClasse.addEventListener('change', applyFilters);
        }
        if (btnResetEmpty) {
            btnResetEmpty.addEventListener('click', function () {
                if (searchInput) searchInput.value = '';
                if (filterClasse) filterClasse.value = '';
                applyFilters();
            });
        }

        // Initialisation des stats
        applyFilters();
    });
}());
