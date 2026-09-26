(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        // 1. Bouton Impression
        document.querySelectorAll('.js-print-page').forEach(function (button) {
            button.addEventListener('click', function () {
                expandAllClasses();
                window.print();
            });
        });

        // 2. Éléments du DOM
        var searchInput = document.getElementById('qrSearchInput');
        var searchClear = document.getElementById('qrSearchClear');
        var filterClasse = document.getElementById('filterClasseSelect');
        var filterCycle = document.getElementById('filterCycleSelect');
        var sortQr = document.getElementById('sortQrSelect');
        var gridSize = document.getElementById('gridSizeSelect');

        var filterStats = document.getElementById('qrFilterStats');
        var filterStatsMobile = document.getElementById('qrFilterStatsMobile');
        var filterBadge = document.getElementById('qrFilterBadge');

        var btnResetEmpty = document.getElementById('btnResetFiltersEmpty');
        var btnResetDrawer = document.getElementById('btnResetQrFilters');

        var noResultsState = document.getElementById('noQrFoundState');
        var classListContainer = document.getElementById('classQrcodeList');
        var chipButtons = Array.from(document.querySelectorAll('.class-filter-chip'));

        // Bouton Tout déplier / Tout replier
        var btnToggleAll = document.getElementById('btnToggleAllClassQrcodes');
        var iconToggleAll = document.getElementById('iconToggleAll');
        var textToggleAll = document.getElementById('textToggleAll');
        var allExpanded = false;

        var classCards = Array.from(document.querySelectorAll('.class-qrcode-card'));
        var totalClassesCount = classCards.length;
        var lastSortMode = null;
        var lastGridMode = null;
        var searchDebounceTimer = null;

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

        // Active Chip State
        var activeChipClasse = '';
        var activeChipCycle = '';

        chipButtons.forEach(function (chip) {
            chip.addEventListener('click', function () {
                chipButtons.forEach(function (c) {
                    c.classList.remove('active', 'btn-primary', 'text-white', 'fw-semibold');
                    c.classList.add('bg-white', 'text-dark');
                });
                chip.classList.add('active', 'btn-primary', 'text-white', 'fw-semibold');
                chip.classList.remove('bg-white', 'text-dark');

                activeChipClasse = chip.dataset.classChip !== undefined ? normalizeStr(chip.dataset.classChip) : '';
                activeChipCycle = chip.dataset.cycleChip !== undefined ? normalizeStr(chip.dataset.cycleChip) : '';

                if (filterClasse) filterClasse.value = activeChipClasse;
                if (filterCycle) filterCycle.value = activeChipCycle;

                applyFilters();
            });
        });

        // Application de la taille de grille
        function updateGridSize() {
            var mode = gridSize ? gridSize.value : 'standard';
            var colClasses = 'col-6 col-sm-4 col-md-3 col-lg-2'; // Default standard

            if (mode === 'compact') {
                colClasses = 'col-4 col-sm-3 col-md-2 col-lg-1';
            } else if (mode === 'large') {
                colClasses = 'col-12 col-sm-6 col-md-4 col-lg-3';
            }

            document.querySelectorAll('.qr-item-col').forEach(function (col) {
                col.className = 'qr-item-col ' + colClasses;
            });
        }

        // Tri des élèves dans chaque classe
        function sortStudents(force) {
            var mode = sortQr ? sortQr.value : 'name_asc';
            if (!force && mode === lastSortMode) return;
            lastSortMode = mode;

            classCards.forEach(function (card) {
                var gridRow = card.querySelector('.qr-grid-row');
                if (!gridRow) return;

                var items = Array.from(gridRow.querySelectorAll('.qr-item-col'));
                items.sort(function (a, b) {
                    if (mode === 'name_asc') {
                        return (a.dataset.nameSort || '').localeCompare(b.dataset.nameSort || '');
                    } else if (mode === 'name_desc') {
                        return (b.dataset.nameSort || '').localeCompare(a.dataset.nameSort || '');
                    } else if (mode === 'id_asc') {
                        return (parseInt(a.dataset.id) || 0) - (parseInt(b.dataset.id) || 0);
                    }
                    return 0;
                });

                items.forEach(function (item) {
                    gridRow.appendChild(item);
                });
            });
        }

        function debouncedApplyFilters() {
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(applyFilters, 280);
        }

        // Action de filtrage centralisée
        function applyFilters() {
            var query = searchInput ? normalizeStr(searchInput.value.trim()) : '';
            var selectedClasse = filterClasse ? normalizeStr(filterClasse.value.trim()) : activeChipClasse;
            var selectedCycle = filterCycle ? normalizeStr(filterCycle.value.trim()) : activeChipCycle;

            if (searchClear) {
                searchClear.classList.toggle('d-none', !query);
            }

            // Calcul des filtres actifs pour le badge
            var activeFilterCount = 0;
            if (query) activeFilterCount++;
            if (selectedClasse) activeFilterCount++;
            if (selectedCycle) activeFilterCount++;
            if (sortQr && sortQr.value !== 'name_asc') activeFilterCount++;
            if (gridSize && gridSize.value !== 'standard') activeFilterCount++;

            if (filterBadge) {
                if (activeFilterCount > 0) {
                    filterBadge.textContent = activeFilterCount;
                    filterBadge.style.display = 'inline-block';
                } else {
                    filterBadge.style.display = 'none';
                }
            }

            var visibleClassesCount = 0;
            var visibleStudentsCount = 0;

            classCards.forEach(function (card) {
                var classeNom = normalizeStr(card.dataset.nom);
                var classeNiveau = normalizeStr(card.dataset.niveau);
                var classeCycle = normalizeStr(card.dataset.cycle);
                var elevesStr = normalizeStr(card.dataset.eleves);

                var items = Array.from(card.querySelectorAll('.qr-item-col'));

                var matchClasse = !selectedClasse || classeNom === selectedClasse;
                var matchCycle = !selectedCycle || classeCycle === selectedCycle || classeNiveau.includes(selectedCycle);
                var matchSearch = !query || classeNom.includes(query) || elevesStr.includes(query);

                if (matchClasse && matchCycle && matchSearch) {
                    var cardVisibleStudents = 0;
                    items.forEach(function (item) {
                        var eleveNom = normalizeStr(item.dataset.eleve);
                        var eleveId = normalizeStr(item.dataset.id);
                        if (!query || eleveNom.includes(query) || classeNom.includes(query) || eleveId.includes(query)) {
                            item.style.display = '';
                            cardVisibleStudents++;
                        } else {
                            item.style.display = 'none';
                        }
                    });

                    if (cardVisibleStudents > 0 || !query) {
                        card.style.display = '';
                        visibleClassesCount++;
                        visibleStudentsCount += cardVisibleStudents;
                    } else {
                        card.style.display = 'none';
                    }

                    // Si recherche active, déplier automatiquement
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

            // Gérer les états d'affichage
            if (noResultsState) {
                noResultsState.style.display = (visibleClassesCount === 0 && totalClassesCount > 0) ? 'block' : 'none';
            }
            if (classListContainer) {
                classListContainer.style.display = (visibleClassesCount === 0 && totalClassesCount > 0) ? 'none' : 'block';
            }

            var statsMsg = visibleClassesCount + ' classe(s) affichée(s) — ' + visibleStudentsCount + ' QR code(s)';
            if (filterStats) {
                filterStats.innerHTML = '<i class="fas fa-layer-group me-1 text-primary"></i>' + statsMsg;
            }
            if (filterStatsMobile) {
                filterStatsMobile.textContent = statsMsg;
            }

            sortStudents(false);
            var gridMode = gridSize ? gridSize.value : 'standard';
            if (gridMode !== lastGridMode) {
                updateGridSize();
                lastGridMode = gridMode;
            }
        }

        function resetAllFilters() {
            if (searchInput) searchInput.value = '';
            if (filterClasse) filterClasse.value = '';
            if (filterCycle) filterCycle.value = '';
            if (sortQr) sortQr.value = 'name_asc';
            if (gridSize) gridSize.value = 'standard';

            activeChipClasse = '';
            activeChipCycle = '';

            chipButtons.forEach(function (c, idx) {
                if (idx === 0) {
                    c.classList.add('active', 'btn-primary', 'text-white', 'fw-semibold');
                    c.classList.remove('bg-white', 'text-dark');
                } else {
                    c.classList.remove('active', 'btn-primary', 'text-white', 'fw-semibold');
                    c.classList.add('bg-white', 'text-dark');
                }
            });

            applyFilters();
        }

        // Événements
        if (searchInput) searchInput.addEventListener('input', debouncedApplyFilters);
        if (searchClear) {
            searchClear.addEventListener('click', function () {
                searchInput.value = '';
                applyFilters();
                searchInput.focus();
            });
        }
        if (filterClasse) {
            filterClasse.addEventListener('change', function () {
                var selectedOption = filterClasse.options[filterClasse.selectedIndex];
                if (selectedOption && selectedOption.dataset.serverUrl) {
                    window.location.href = selectedOption.dataset.serverUrl;
                    return;
                }
                activeChipClasse = filterClasse.value;
                applyFilters();
            });
        }
        if (filterCycle) {
            filterCycle.addEventListener('change', function () {
                activeChipCycle = filterCycle.value;
                applyFilters();
            });
        }
        if (sortQr) sortQr.addEventListener('change', function () {
            sortStudents(false);
            applyFilters();
        });
        if (gridSize) gridSize.addEventListener('change', applyFilters);

        if (btnResetEmpty) btnResetEmpty.addEventListener('click', resetAllFilters);
        if (btnResetDrawer) btnResetDrawer.addEventListener('click', resetAllFilters);

        // Initialisation
        sortStudents(true);
        applyFilters();
    });
}());
