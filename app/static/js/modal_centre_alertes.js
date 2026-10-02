/**
 * modal_centre_alertes.js
 * Gestion réactive du Centre d'Alertes Scolaires (Modal Popup)
 * Chargement AJAX, compteurs dynamiques en temps réel, filtres instantanés
 */
(function() {
    'use strict';

    let allAlerts = [];
    let classesList = [];
    let activeFilter = 'all';
    let searchQuery = '';
    let selectedClassId = 'all';
    let isFetching = false;

    // Éléments du DOM
    const modalEl = document.getElementById('modalCentreAlertes');
    const headerBadge = document.getElementById('modalAlertesHeaderBadge');
    const topbarBadge = document.getElementById('topbarAlertesBadge');
    const listContainer = document.getElementById('modalAlertesList');
    const loadingState = document.getElementById('modalAlertesLoading');
    const emptyState = document.getElementById('modalAlertesEmpty');
    const emptyMsg = document.getElementById('modalAlertesEmptyMsg');
    const footerStats = document.getElementById('modalAlertesFooterStats');
    const searchInput = document.getElementById('modalAlertesSearchInput');
    const clearSearchBtn = document.getElementById('btnModalClearSearch');
    const classFilterSelect = document.getElementById('modalAlertesClassFilter');
    const tabsContainer = document.getElementById('modalAlertesTabs');
    const btnMarkAllRead = document.getElementById('btnModalMarkAllRead');
    const btnRefresh = document.getElementById('btnModalRefreshAlertes');

    // Compteurs des onglets
    const tabCountAll = document.getElementById('mTabCountAll');
    const tabCountJamaisPaye = document.getElementById('mTabCountJamaisPaye');
    const tabCountPaiements = document.getElementById('mTabCountPaiements');
    const tabCountAbsences = document.getElementById('mTabCountAbsences');
    const tabCountNotes = document.getElementById('mTabCountNotes');
    const tabCountTraitees = document.getElementById('mTabCountTraitees');

    /**
     * Met à jour les badges de comptage (topbar, modal header, onglets)
     */
    function updateCounts(stats) {
        if (!stats) {
            const actives = allAlerts.filter(a => !a.traitee);
            stats = {
                actives: actives.length,
                total: allAlerts.length,
                jamais_paye: actives.filter(a => a.source === 'Paiements' && a.jamais_paye).length,
                paiements: actives.filter(a => a.source === 'Paiements').length,
                absences: actives.filter(a => a.source === 'Absences').length,
                notes: actives.filter(a => a.source === 'Notes').length,
                traitees: allAlerts.filter(a => a.traitee).length,
                urgentes: actives.filter(a => a.type === 'danger').length,
            };
        }

        // Badge Topbar
        if (topbarBadge) {
            if (stats.actives > 0) {
                topbarBadge.textContent = stats.actives;
                topbarBadge.classList.remove('d-none');
                if (stats.urgentes > 0 || stats.jamais_paye > 0) {
                    topbarBadge.className = 'badge bg-danger rounded-pill px-1.5 py-0.5 animate-pulse';
                } else {
                    topbarBadge.className = 'badge bg-warning text-dark rounded-pill px-1.5 py-0.5';
                }
            } else {
                topbarBadge.textContent = '0';
                topbarBadge.className = 'badge bg-success rounded-pill px-1.5 py-0.5';
            }
        }

        // Badge En-tête Modal
        if (headerBadge) {
            if (stats.actives > 0) {
                headerBadge.textContent = `${stats.actives} active${stats.actives > 1 ? 's' : ''}`;
                headerBadge.classList.remove('d-none');
            } else {
                headerBadge.textContent = 'À jour';
                headerBadge.className = 'badge bg-success rounded-pill px-2 py-0.5 fs-7';
            }
        }

        // Badges Onglets
        if (tabCountAll) tabCountAll.textContent = stats.actives ?? 0;
        if (tabCountJamaisPaye) tabCountJamaisPaye.textContent = stats.jamais_paye ?? 0;
        if (tabCountPaiements) tabCountPaiements.textContent = stats.paiements ?? 0;
        if (tabCountAbsences) tabCountAbsences.textContent = stats.absences ?? 0;
        if (tabCountNotes) tabCountNotes.textContent = stats.notes ?? 0;
        if (tabCountTraitees) tabCountTraitees.textContent = stats.traitees ?? 0;

        // Footer
        if (footerStats) {
            footerStats.textContent = `${stats.actives} alerte${stats.actives > 1 ? 's' : ''} active${stats.actives > 1 ? 's' : ''} sur ${stats.total ?? allAlerts.length} au total`;
        }
    }

    /**
     * Récupération des alertes depuis le backend
     */
    async function fetchAlerts(showLoader = true) {
        if (isFetching) return;
        isFetching = true;

        if (showLoader && loadingState) {
            loadingState.classList.remove('d-none');
            if (emptyState) emptyState.classList.add('d-none');
            if (listContainer) listContainer.classList.add('d-none');
        }

        try {
            const res = await fetch('/api/alertes', {
                headers: { 'Accept': 'application/json' },
                cache: 'no-cache'
            });

            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            allAlerts = data.alertes || [];
            updateCounts(data.stats);
            populateClassFilter();
            renderAlerts();
        } catch (err) {
            console.error('Erreur chargement alertes:', err);
            if (loadingState) loadingState.classList.add('d-none');
            if (emptyState) {
                emptyState.classList.remove('d-none');
                if (emptyMsg) emptyMsg.textContent = 'Impossible de charger les alertes pour le moment.';
            }
        } finally {
            isFetching = false;
        }
    }

    /**
     * Comptage léger initial pour le badge Topbar sans charger toutes les données
     */
    async function checkQuickCount() {
        try {
            const res = await fetch('/api/alertes/count', {
                headers: { 'Accept': 'application/json' },
                cache: 'no-cache'
            });
            if (res.ok) {
                const stats = await res.json();
                updateCounts(stats);
            }
        } catch (e) {
            // Ignorer silencieusement si hors-ligne ou non disponible
        }
    }

    /**
     * Remplissage du sélecteur de classes
     */
    function populateClassFilter() {
        if (!classFilterSelect) return;
        const classesMap = new Map();
        allAlerts.forEach(a => {
            if (a.classe_id && a.classe_nom) {
                classesMap.set(String(a.classe_id), a.classe_nom);
            }
        });

        const currentVal = classFilterSelect.value;
        classFilterSelect.innerHTML = '<option value="all">Toutes les classes</option>';
        Array.from(classesMap.entries())
            .sort((a, b) => a[1].localeCompare(b[1]))
            .forEach(([id, nom]) => {
                const opt = document.createElement('option');
                opt.value = id;
                opt.textContent = nom;
                classFilterSelect.appendChild(opt);
            });

        if (classesMap.has(currentVal)) {
            classFilterSelect.value = currentVal;
        }
    }

    /**
     * Rendu HTML des alertes filtrées
     */
    function renderAlerts() {
        if (loadingState) loadingState.classList.add('d-none');

        // Filtrage des alertes
        const q = searchQuery.toLowerCase().trim();
        const filtered = allAlerts.filter(a => {
            // Filtre onglet
            if (activeFilter === 'jamais_paye') {
                if (a.traitee || !a.jamais_paye) return false;
            } else if (activeFilter === 'traitee') {
                if (!a.traitee) return false;
            } else if (activeFilter === 'all') {
                if (a.traitee) return false;
            } else {
                if (a.traitee || a.source !== activeFilter) return false;
            }

            // Filtre classe
            if (selectedClassId !== 'all') {
                if (String(a.classe_id) !== String(selectedClassId)) return false;
            }

            // Filtre textuel
            if (q) {
                const haystack = `${a.eleve_nom || ''} ${a.classe_nom || ''} ${a.message || ''} ${a.titre || ''} ${a.valeur_cle || ''}`.toLowerCase();
                if (!haystack.includes(q)) return false;
            }

            return true;
        });

        // Affichage état vide ou liste
        if (filtered.length === 0) {
            if (listContainer) listContainer.classList.add('d-none');
            if (emptyState) {
                emptyState.classList.remove('d-none');
                if (emptyMsg) {
                    if (activeFilter === 'jamais_paye') {
                        emptyMsg.textContent = 'Aucun élève en défaut total de paiement.';
                    } else if (activeFilter === 'traitee') {
                        emptyMsg.textContent = 'Aucune alerte traitée pour le moment.';
                    } else if (q) {
                        emptyMsg.textContent = `Aucune alerte correspondant à "${q}".`;
                    } else {
                        emptyMsg.textContent = 'Tout est sous contrôle dans cette catégorie !';
                    }
                }
            }
            return;
        }

        if (emptyState) emptyState.classList.add('d-none');
        if (listContainer) {
            listContainer.classList.remove('d-none');
            listContainer.innerHTML = filtered.map(a => generateAlertCardHtml(a)).join('');
        }
    }

    /**
     * Génération du code HTML d'une carte d'alerte
     */
    function generateAlertCardHtml(a) {
        const isTraitee = Boolean(a.traitee);
        const isDanger = a.type === 'danger';
        const isWarning = a.type === 'warning';
        const borderColor = isTraitee ? 'border-success' : (isDanger ? 'border-danger' : (isWarning ? 'border-warning' : 'border-primary'));
        const bgSubtle = isTraitee ? 'bg-white' : (isDanger ? 'bg-danger-subtle bg-opacity-10' : 'bg-white');

        // Initiales élève
        const initials = (a.eleve_nom || 'Élève').split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();

        // Badge catégorie / source
        let sourceBadge = '';
        if (a.source === 'Paiements') {
            sourceBadge = `<span class="badge bg-danger-subtle text-danger border border-danger-subtle"><i class="fas fa-money-bill-wave me-1"></i>Paiement</span>`;
        } else if (a.source === 'Absences') {
            sourceBadge = `<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle"><i class="fas fa-calendar-times me-1"></i>Absence</span>`;
        } else if (a.source === 'Notes') {
            sourceBadge = `<span class="badge bg-primary-subtle text-primary border border-primary-subtle"><i class="fas fa-graduation-cap me-1"></i>Notes</span>`;
        }

        // Badge Jamais payé
        const jamaisPayeBadge = a.jamais_paye ? `<span class="badge bg-danger text-white"><i class="fas fa-hand-holding-usd me-1"></i>Jamais payé (0 F)</span>` : '';

        // Badge Urgence
        const urgencyBadge = isTraitee ? `<span class="badge bg-success"><i class="fas fa-check me-1"></i>Traitée</span>` :
            (isDanger ? `<span class="badge bg-danger">Urgente</span>` : (isWarning ? `<span class="badge bg-warning text-dark">Important</span>` : `<span class="badge bg-info text-dark">Info</span>`));

        // Clé numérique
        const valeurCleBadge = a.valeur_cle ? `<span class="badge ${isDanger ? 'bg-danger text-white' : 'bg-light text-dark border'} fw-bold">${escapeHtml(a.valeur_cle)}</span>` : '';

        // URL WhatsApp
        let whatsappBtn = '';
        if (a.contact_parent) {
            const cleanPhone = a.contact_parent.replace(/[^0-9]/g, '');
            const defaultMsg = encodeURIComponent(`Bonjour, message de l'établissement concernant votre enfant ${a.eleve_nom || ''} (${a.classe_nom || ''}) : ${a.message || ''}`);
            whatsappBtn = `
                <a href="https://wa.me/${cleanPhone}?text=${defaultMsg}" target="_blank" rel="noopener" 
                   class="btn btn-sm btn-outline-success rounded-pill px-2.5 py-1 d-inline-flex align-items-center gap-1" 
                   title="Contacter le parent sur WhatsApp">
                    <i class="fab fa-whatsapp"></i>
                    <span class="d-none d-md-inline">WhatsApp</span>
                </a>
            `;
        }

        // Bouton voir dossier élève
        const dossierUrl = a.lien || (a.eleve_id ? `/eleve/${a.eleve_id}` : '#');

        return `
            <div class="card ${borderColor} ${bgSubtle} shadow-2xs rounded-3 p-3 transition-all" data-alert-id="${escapeHtml(a.id)}">
                <div class="d-flex align-items-start justify-content-between gap-2 mb-2 flex-wrap">
                    <div class="d-flex align-items-center gap-1.5 flex-wrap">
                        ${urgencyBadge}
                        ${jamaisPayeBadge}
                        ${sourceBadge}
                        ${valeurCleBadge}
                    </div>
                    <span class="small text-muted" style="font-size: 0.75rem;">
                        <i class="far fa-clock me-1"></i>${escapeHtml(a.date || '')}
                    </span>
                </div>

                <div class="d-flex align-items-start gap-2.5 mb-2">
                    <div class="rounded-circle d-flex align-items-center justify-content-center text-white fw-bold flex-shrink-0 ${isDanger ? 'bg-danger' : (isWarning ? 'bg-warning' : 'bg-primary')}" 
                         style="width: 38px; height: 38px; font-size: 0.85rem;">
                        ${initials}
                    </div>
                    <div class="flex-grow-1 min-w-0">
                        <div class="d-flex align-items-baseline gap-2 flex-wrap">
                            <a href="${dossierUrl}" class="fw-bold text-dark text-decoration-none hover-primary">
                                ${escapeHtml(a.eleve_nom || 'Élève inconnu')}
                            </a>
                            <span class="badge bg-secondary-subtle text-secondary rounded-pill" style="font-size: 0.72rem;">
                                ${escapeHtml(a.classe_nom || 'Sans classe')}
                            </span>
                        </div>
                        <p class="small text-secondary mb-0 mt-0.5" style="line-height: 1.35;">
                            ${escapeHtml(a.message || a.titre || '')}
                        </p>
                    </div>
                </div>

                <!-- Barre d'actions rapides -->
                <div class="d-flex align-items-center justify-content-between gap-2 pt-2 border-top flex-wrap">
                    <div class="d-flex align-items-center gap-1.5 flex-wrap">
                        <a href="${dossierUrl}" class="btn btn-sm btn-outline-primary rounded-pill px-2.5 py-1 d-inline-flex align-items-center gap-1" title="Voir la fiche de l'élève">
                            <i class="fas fa-external-link-alt"></i>
                            <span>Fiche élève</span>
                        </a>
                        ${whatsappBtn}
                    </div>
                    <div>
                        <button type="button" class="btn btn-sm ${isTraitee ? 'btn-success' : 'btn-outline-dark'} rounded-pill px-2.5 py-1 d-inline-flex align-items-center gap-1 btn-toggle-traitee" 
                                data-action="toggle-alert" data-alert-id="${escapeHtml(a.id)}">
                            <i class="fas ${isTraitee ? 'fa-undo' : 'fa-check'}"></i>
                            <span>${isTraitee ? 'Réactiver' : 'Marquer traité'}</span>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Basculer le statut traité/actif d'une alerte
     */
    async function toggleAlertStatus(alertId) {
        const alerte = allAlerts.find(a => a.id === alertId);
        if (!alerte) return;

        // Optimistic UI update
        const prevStatus = alerte.traitee;
        alerte.traitee = !prevStatus;
        updateCounts();
        renderAlerts();

        try {
            const res = await fetch(`/api/alertes/${encodeURIComponent(alertId)}/read`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({ action: 'toggle' })
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            alerte.traitee = data.is_traitee;
            updateCounts();
            renderAlerts();
        } catch (err) {
            console.error('Erreur bascule statut alerte:', err);
            // Rollback en cas d'erreur
            alerte.traitee = prevStatus;
            updateCounts();
            renderAlerts();
        }
    }

    /**
     * Marquer toutes les alertes affichées comme traitées
     */
    async function markAllAlertsRead() {
        if (!confirm('Voulez-vous marquer toutes les alertes actuelles comme traitées ?')) {
            return;
        }

        try {
            if (btnMarkAllRead) btnMarkAllRead.disabled = true;
            const res = await fetch('/api/alertes/all/read', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({ action: 'read_all' })
            });

            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            allAlerts.forEach(a => a.traitee = true);
            updateCounts();
            renderAlerts();
        } catch (err) {
            console.error('Erreur traitement global:', err);
            alert('Impossible de marquer toutes les alertes comme traitées.');
        } finally {
            if (btnMarkAllRead) btnMarkAllRead.disabled = false;
        }
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Écouteurs d'événements
    document.addEventListener('DOMContentLoaded', function() {
        // Comptage rapide au démarrage de la page
        checkQuickCount();

        // Ouverture du modal via query param ?open_alertes=1
        if (window.location.search.includes('open_alertes=1')) {
            const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
            if (modalInstance) {
                modalInstance.show();
            }
        }

        // Chargement quand le modal s'ouvre
        if (modalEl) {
            modalEl.addEventListener('show.bs.modal', function() {
                fetchAlerts(allAlerts.length === 0);
            });
        }

        // Clics sur les onglets de filtres
        if (tabsContainer) {
            tabsContainer.addEventListener('click', function(e) {
                const btn = e.target.closest('.modal-alert-tab-btn');
                if (!btn) return;
                tabsContainer.querySelectorAll('.modal-alert-tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                activeFilter = btn.dataset.filter || 'all';
                renderAlerts();
            });
        }

        // Recherche en temps réel
        if (searchInput) {
            searchInput.addEventListener('input', function() {
                searchQuery = this.value;
                if (clearSearchBtn) {
                    clearSearchBtn.classList.toggle('d-none', !this.value);
                }
                renderAlerts();
            });
        }

        if (clearSearchBtn) {
            clearSearchBtn.addEventListener('click', function() {
                if (searchInput) {
                    searchInput.value = '';
                    searchQuery = '';
                    this.classList.add('d-none');
                    renderAlerts();
                }
            });
        }

        // Filtre de classe
        if (classFilterSelect) {
            classFilterSelect.addEventListener('change', function() {
                selectedClassId = this.value;
                renderAlerts();
            });
        }

        // Bouton tout marquer comme traité
        if (btnMarkAllRead) {
            btnMarkAllRead.addEventListener('click', markAllAlertsRead);
        }

        // Bouton rafraîchir
        if (btnRefresh) {
            btnRefresh.addEventListener('click', function() {
                fetchAlerts(true);
            });
        }

        // Délégation d'événements pour le bouton Marquer comme traité sur chaque carte
        if (listContainer) {
            listContainer.addEventListener('click', function(e) {
                const btn = e.target.closest('[data-action="toggle-alert"]');
                if (!btn) return;
                const alertId = btn.dataset.alertId;
                if (alertId) {
                    toggleAlertStatus(alertId);
                }
            });
        }
    });

    // Expose globalement pour interaction si besoin
    window.KlasoraAlertesModal = {
        open: function() {
            if (modalEl) {
                const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
                modalInstance.show();
            }
        },
        refresh: fetchAlerts
    };
})();
