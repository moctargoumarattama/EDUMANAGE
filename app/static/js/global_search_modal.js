/**
 * KLASORA - Script du Modal Popup de Recherche Globale Instantanée
 * Remplace l'ancienne page dédiée /recherche par une expérience fluide sans rechargement.
 */
document.addEventListener('DOMContentLoaded', function () {
    const modalEl = document.getElementById('modalRechercheGlobale');
    const input = document.getElementById('globalModalSearchInput');
    const clearBtn = document.getElementById('modalSearchClearBtn');
    const searchIcon = document.getElementById('modalSearchIcon');
    const filterBtns = document.querySelectorAll('.modal-filter-btn');
    const resultsContainer = document.getElementById('modalSearchResults');
    const loadingIndicator = document.getElementById('modalSearchLoading');

    let currentType = 'all';
    let debounceTimer = null;
    let abortController = null;

    if (!modalEl || !input || !resultsContainer) return;

    // 1. Focus automatique du champ de recherche à l'ouverture du modal
    modalEl.addEventListener('shown.bs.modal', function () {
        input.focus();
        input.select();
    });

    // 2. Gestion du bouton Effacer (✕)
    function updateClearBtn() {
        if (!clearBtn) return;
        if (input.value.trim().length > 0) {
            clearBtn.classList.remove('d-none');
        } else {
            clearBtn.classList.add('d-none');
        }
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            input.value = '';
            updateClearBtn();
            performSearch('');
            input.focus();
        });
    }

    // 3. Gestion des boutons de filtre par catégorie (Tous, Élèves, Professeurs, Cours...)
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            filterBtns.forEach(b => {
                b.classList.remove('active', 'btn-primary');
                b.classList.add('btn-outline-secondary');
            });
            this.classList.add('active', 'btn-primary');
            this.classList.remove('btn-outline-secondary');

            currentType = this.dataset.type || 'all';
            performSearch(input.value.trim());
        });
    });

    // 4. Fonction de recherche asynchrone AJAX
    async function performSearch(query) {
        updateClearBtn();

        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();

        // Si la saisie est vide, recharger l'état d'attente
        if (!query) {
            if (loadingIndicator) loadingIndicator.classList.add('d-none');
            resultsContainer.classList.remove('d-none');
            // Appel AJAX léger pour récupérer l'état initial
            try {
                const resp = await fetch(`/recherche?q=&type=${encodeURIComponent(currentType)}&ajax=1`, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    signal: abortController.signal
                });
                if (resp.ok) {
                    const html = await resp.text();
                    resultsContainer.innerHTML = html;
                }
            } catch (e) {
                // Requête annulée ou erreur réseau
            }
            return;
        }

        // Afficher l'indicateur de chargement
        if (searchIcon) searchIcon.className = 'fas fa-spinner fa-spin position-absolute top-50 translate-middle-y text-primary fs-5 ms-3 pointer-events-none';
        if (loadingIndicator) loadingIndicator.classList.remove('d-none');
        resultsContainer.classList.add('opacity-50');

        try {
            const url = `/recherche?q=${encodeURIComponent(query)}&type=${encodeURIComponent(currentType)}&ajax=1`;
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: abortController.signal
            });

            if (!response.ok) throw new Error('Erreur HTTP ' + response.status);

            const html = await response.text();
            resultsContainer.innerHTML = html;
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Erreur recherche globale:', error);
            }
        } finally {
            if (searchIcon) searchIcon.className = 'fas fa-search position-absolute top-50 translate-middle-y text-primary fs-5 ms-3 pointer-events-none';
            if (loadingIndicator) loadingIndicator.classList.add('d-none');
            resultsContainer.classList.remove('opacity-50', 'd-none');
        }
    }

    // 5. Écoute de la saisie utilisateur avec Debounce fluide de 200ms
    input.addEventListener('input', function () {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => {
            performSearch(input.value.trim());
        }, 200);
    });

    // 6. Ouverture automatique si ?open_search=1 est présent dans l'URL
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('open_search')) {
        const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
        modalInstance.show();

        // Nettoyer l'URL sans recharger
        urlParams.delete('open_search');
        const newSearch = urlParams.toString();
        const newUrl = window.location.pathname + (newSearch ? '?' + newSearch : '');
        window.history.replaceState({}, '', newUrl);
    }
});

