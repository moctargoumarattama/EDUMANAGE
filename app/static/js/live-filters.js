/**
 * KLASORA Live Filters & Instant Search Module
 * Fournit une expérience de filtrage temps réel fluide, débouncée (300ms),
 * avec AbortController, synchronisation URL et gestion des sélecteurs dépendants.
 */

(function () {
    'use strict';

    function debounce(func, wait = 300) {
        let timeout;
        return function (...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    class LiveFilterManager {
        constructor(config) {
            this.container = document.querySelector(config.containerSelector);
            if (!this.container) return;

            this.endpoint = config.endpoint || window.location.pathname;
            this.resultsSelector = config.resultsSelector || '#results-container';
            this.countSelector = config.countSelector || '#results-count';
            this.emptySelector = config.emptySelector || '#no-results';
            this.searchSelector = config.searchSelector || 'input[type="search"], input.live-search';
            this.filterSelectors = config.filterSelectors || 'select.live-filter, input.live-filter';
            this.resetSelector = config.resetSelector || '.btn-reset-filters';
            this.loadingClass = config.loadingClass || 'loading-active';
            this.onResultsUpdated = config.onResultsUpdated || null;
            this.paramNames = config.paramNames || {};

            this.currentAbortController = null;
            this.currentPage = 1;

            this.init();
        }

        init() {
            // Recherche textuelle avec debounce 300ms
            const searchInputs = this.container.querySelectorAll(this.searchSelector);
            searchInputs.forEach(input => {
                input.addEventListener('input', debounce(() => {
                    this.currentPage = 1;
                    this.triggerFetch();
                }, 300));
            });

            // Filtres directs (selects, dates, cases à cocher)
            const filterInputs = this.container.querySelectorAll(this.filterSelectors);
            filterInputs.forEach(input => {
                input.addEventListener('change', () => {
                    this.currentPage = 1;
                    this.triggerFetch();
                });
            });

            // Bouton de réinitialisation
            const resetBtns = this.container.querySelectorAll(this.resetSelector);
            resetBtns.forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.reset();
                });
            });

            // Délégation pour la pagination
            document.addEventListener('click', (e) => {
                const pageLink = e.target.closest('.live-page-link, [data-live-page]');
                if (pageLink && this.container.contains(pageLink.closest('.pagination-container') || pageLink)) {
                    e.preventDefault();
                    const targetPage = pageLink.dataset.livePage || pageLink.getAttribute('href');
                    if (targetPage) {
                        const pageNum = parseInt(targetPage.replace(/[^0-9]/g, ''), 10);
                        if (!isNaN(pageNum) && pageNum !== this.currentPage) {
                            this.currentPage = pageNum;
                            this.triggerFetch();
                        }
                    }
                }
            });
        }

        collectParams() {
            const params = new URLSearchParams();

            // Inputs texte
            const searchInputs = this.container.querySelectorAll(this.searchSelector);
            searchInputs.forEach(input => {
                const val = input.value.trim();
                const name = input.name || input.id || 'search';
                if (val) params.set(name, val);
            });

            // Selects & autres filtres
            const filterInputs = this.container.querySelectorAll(this.filterSelectors);
            filterInputs.forEach(input => {
                const name = input.name || input.id;
                if (!name) return;

                if (input.type === 'checkbox') {
                    if (input.checked) params.set(name, input.value || '1');
                } else if (input.type === 'radio') {
                    if (input.checked) params.set(name, input.value);
                } else {
                    const val = input.value;
                    if (val && val !== 'all' && val !== '') {
                        params.set(name, val);
                    }
                }
            });

            if (this.currentPage > 1) {
                params.set('page', this.currentPage);
            }

            return params;
        }

        async triggerFetch() {
            const params = this.collectParams();

            // Synchronisation de l'URL dans l'historique sans rechargement
            const cleanUrl = `${window.location.pathname}${params.toString() ? '?' + params.toString() : ''}`;
            window.history.replaceState({ path: cleanUrl }, '', cleanUrl);

            // Annuler l'ancienne requête si elle était toujours en cours
            if (this.currentAbortController) {
                this.currentAbortController.abort();
            }
            this.currentAbortController = new AbortController();
            const signal = this.currentAbortController.signal;

            const resultsContainer = document.querySelector(this.resultsSelector);
            if (resultsContainer) {
                resultsContainer.setAttribute('aria-busy', 'true');
                resultsContainer.classList.add(this.loadingClass);
            }

            try {
                // Ajouter ajax=1 ou en-tête XMLHttpRequest
                params.set('ajax', '1');
                const separator = this.endpoint.includes('?') ? '&' : '?';
                const fetchUrl = `${this.endpoint}${separator}${params.toString()}`;

                const response = await fetch(fetchUrl, {
                    method: 'GET',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json, text/html'
                    },
                    signal: signal
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }

                const contentType = response.headers.get('content-type') || '';
                if (contentType.includes('application/json')) {
                    const data = await response.json();
                    this.renderData(data);
                } else {
                    const html = await response.text();
                    this.renderHtml(html);
                }

            } catch (err) {
                if (err.name === 'AbortError') {
                    // Requête annulée intentionnellement par une nouvelle frappe, ignorer
                    return;
                }
                console.error('[LiveFilter] Erreur chargement:', err);
                const countBadge = document.querySelector(this.countSelector);
                if (countBadge) countBadge.textContent = 'Erreur';
            } finally {
                if (resultsContainer) {
                    resultsContainer.removeAttribute('aria-busy');
                    resultsContainer.classList.remove(this.loadingClass);
                }
            }
        }

        renderData(data) {
            const resultsContainer = document.querySelector(this.resultsSelector);
            const countBadge = document.querySelector(this.countSelector);

            if (data.total !== undefined && countBadge) {
                countBadge.textContent = `${data.total} résultat${data.total > 1 ? 's' : ''}`;
            }

            if (resultsContainer && data.html !== undefined) {
                if (data.total === 0) {
                    resultsContainer.innerHTML = `
                        <div class="card border-0 shadow-sm text-center py-5 my-3">
                            <div class="card-body">
                                <i class="fas fa-search text-muted fa-3x mb-3 opacity-50"></i>
                                <h5 class="fw-bold text-gray-800 mb-1">Aucun résultat ne correspond à vos critères</h5>
                                <p class="text-muted small mb-0">Essayez de modifier votre recherche ou vos filtres.</p>
                            </div>
                        </div>
                    `;
                } else {
                    resultsContainer.innerHTML = data.html;
                }
            }

            if (typeof this.onResultsUpdated === 'function') {
                this.onResultsUpdated(data);
            }
        }

        renderHtml(html) {
            const resultsContainer = document.querySelector(this.resultsSelector);
            if (resultsContainer) {
                resultsContainer.innerHTML = html;
            }
            if (typeof this.onResultsUpdated === 'function') {
                this.onResultsUpdated({ html });
            }
        }

        reset() {
            const searchInputs = this.container.querySelectorAll(this.searchSelector);
            searchInputs.forEach(input => { input.value = ''; });

            const filterInputs = this.container.querySelectorAll(this.filterSelectors);
            filterInputs.forEach(input => {
                if (input.tagName === 'SELECT') {
                    input.selectedIndex = 0;
                } else if (input.type === 'checkbox' || input.type === 'radio') {
                    input.checked = false;
                } else {
                    input.value = '';
                }
            });

            this.currentPage = 1;
            this.triggerFetch();
        }
    }

    /**
     * Liaison de sélecteurs dépendants sécurisée (ex: Niveau -> Classe, Classe -> Cours, Classe -> Élèves)
     */
    function bindDependentSelect({
        sourceSelect,
        targetSelect,
        urlBuilder,
        emptyLabel = '-- Tous --',
        mapOption = (item) => ({ value: item.id, label: item.nom }),
        onChange = null
    }) {
        const source = typeof sourceSelect === 'string' ? document.querySelector(sourceSelect) : sourceSelect;
        const target = typeof targetSelect === 'string' ? document.querySelector(targetSelect) : targetSelect;

        if (!source || !target) return;

        let activeController = null;

        source.addEventListener('change', async () => {
            const val = source.value;
            target.innerHTML = `<option value="">${emptyLabel}</option>`;

            if (!val || val === 'all') {
                target.disabled = false;
                if (onChange) onChange([]);
                return;
            }

            if (activeController) activeController.abort();
            activeController = new AbortController();

            target.disabled = true;
            try {
                const url = urlBuilder(val);
                const res = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    signal: activeController.signal
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);

                const items = await res.json();
                const list = Array.isArray(items) ? items : (items.eleves || items.classes || items.cours || []);

                target.innerHTML = `<option value="">${emptyLabel}</option>`;
                list.forEach(item => {
                    const opt = mapOption(item);
                    const el = document.createElement('option');
                    el.value = opt.value;
                    el.textContent = opt.label;
                    target.appendChild(el);
                });
                target.disabled = false;

                if (onChange) onChange(list);
            } catch (err) {
                if (err.name !== 'AbortError') {
                    console.error('[bindDependentSelect] Erreur:', err);
                    target.disabled = false;
                }
            }
        });
    }

    // Export global
    window.KlasoraLiveFilter = {
        Manager: LiveFilterManager,
        bindDependentSelect: bindDependentSelect,
        debounce: debounce
    };
})();

