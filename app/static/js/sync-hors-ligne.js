(function () {
    'use strict';

    const elements = {};
    let initialized = false;
    let lastSyncTime = localStorage.getItem('edumanageLastSync') || 'Jamais';

    function get(id) {
        return document.getElementById(id);
    }

    function setAlert(message, type) {
        const alert = elements.alert || get('syncAlert');
        if (!alert) return;
        alert.className = `alert alert-${type}`;
        alert.innerHTML = message;
        alert.classList.remove('d-none');
        window.setTimeout(() => alert.classList.add('d-none'), 5000);
    }

    function formatType(type) {
        const labels = {
            note: 'Note',
            absence: 'Absence',
            paiement: 'Paiement'
        };
        return labels[type] || 'Élément';
    }

    async function updateConnection() {
        const connText = elements.connectionText || get('connectionText');
        const connIcon = elements.connectionIcon || get('connectionIcon');

        console.log('[SYNC UI] Éléments DOM trouvés:', {
            connectionText: !!connText,
            connectionIcon: !!connIcon
        });

        let online = false;
        try {
            if (connText) connText.textContent = 'Vérification...';
            if (window.offlineManager && typeof window.offlineManager.updateOnlineStatus === 'function') {
                online = await window.offlineManager.updateOnlineStatus();
            } else if (window.offlineManager && typeof window.offlineManager.checkServerConnectivity === 'function') {
                online = await window.offlineManager.checkServerConnectivity();
                window.offlineManager.isOnline = online;
            } else {
                if (navigator.onLine) {
                    try {
                        const controller = new AbortController();
                        const timeoutId = setTimeout(() => controller.abort(), 4000);
                        const res = await fetch('/api/connectivity', { cache: 'no-store', signal: controller.signal });
                        clearTimeout(timeoutId);
                        if (res.ok) {
                            const data = await res.json();
                            console.log('[SYNC UI] Réponse reçue (direct fetch):', data);
                            online = !!(data && data.online === true);
                        } else {
                            console.log('[SYNC UI] Direct fetch status non-OK:', res.status);
                            online = false;
                        }
                    } catch (e) {
                        console.log('[SYNC UI] Erreur fetch direct:', e);
                        online = false;
                    }
                } else {
                    console.log('[SYNC UI] navigator.onLine est false');
                    online = false;
                }
            }
        } catch (err) {
            console.error('[SYNC UI] Exception dans updateConnection:', err);
            online = false;
        }

        const textToApply = online ? 'En ligne' : 'Hors ligne';
        console.log('[SYNC UI] online=true ?', online);
        console.log('[SYNC UI] Texte appliqué:', textToApply);

        if (connText) {
            connText.textContent = textToApply;
        }
        if (connIcon) {
            connIcon.className = online ? 'sync-status-icon is-online' : 'sync-status-icon is-offline';
            connIcon.innerHTML = online
                ? '<i class="fas fa-wifi"></i>'
                : '<i class="fas fa-plug-circle-xmark"></i>';
        }

        return online;
    }

    function renderConflictCard(item) {
        const isAdmin = window.offlineManager && typeof window.offlineManager.isAdmin === 'function' && window.offlineManager.isAdmin();
        const canArbitrate = isAdmin || (item.can_arbitrate === true);
        const lockedByAdmin = item.locked_by_admin === true;

        let detailsHtml = '';
        if (item.type === 'note') {
            const clientVal = item.client_value !== undefined ? item.client_value : item.valeur;
            const serverVal = item.server_value !== undefined ? item.server_value : 'Inconnue / Modifiée';
            detailsHtml = `
                <div class="row g-2 mt-2 mb-2 p-2 bg-light rounded-3 border">
                    <div class="col-sm-6">
                        <span class="text-muted small d-block">Votre note (locale) :</span>
                        <strong class="text-primary">${clientVal}/20</strong>
                    </div>
                    <div class="col-sm-6">
                        <span class="text-muted small d-block">Note sur le serveur :</span>
                        <strong class="text-success">${serverVal}${typeof serverVal === 'number' ? '/20' : ''}</strong>
                    </div>
                </div>
            `;
        } else if (item.type === 'absence') {
            const clientMotif = item.motif || (item.client_value ? item.client_value.motif : 'Non spécifié');
            const serverMotif = item.server_value ? (item.server_value.motif || 'Non spécifié') : 'Modifiée sur le serveur';
            detailsHtml = `
                <div class="row g-2 mt-2 mb-2 p-2 bg-light rounded-3 border">
                    <div class="col-sm-6">
                        <span class="text-muted small d-block">Votre absence (locale) :</span>
                        <strong class="text-primary">${item.date_absence || ''} (${clientMotif})</strong>
                    </div>
                    <div class="col-sm-6">
                        <span class="text-muted small d-block">Absence sur le serveur :</span>
                        <strong class="text-success">${serverMotif}</strong>
                    </div>
                </div>
            `;
        }

        let adminNotice = '';
        if (lockedByAdmin && !isAdmin) {
            adminNotice = `
                <div class="badge bg-danger-subtle text-danger border border-danger-subtle p-2 mb-2 w-100 text-start text-wrap">
                    <i class="fas fa-lock me-1"></i> Cette entrée a été modifiée par l'administration de l'école. La version de l'administration est prioritaire et ne peut pas être écrasée.
                </div>
            `;
        }

        const forceBtn = canArbitrate ? `
            <button type="button" class="btn btn-sm btn-outline-danger btn-resolve-conflict" data-op-id="${item.client_op_id}" data-action="force">
                <i class="fas fa-gavel me-1"></i> Arbitrer / Imposer ma version
            </button>
        ` : '';

        return `
            <div class="card border-warning shadow-sm">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap mb-2">
                        <div>
                            <span class="badge bg-warning text-dark me-2">Conflit • ${formatType(item.type)}</span>
                            <small class="text-muted">${new Date(item.timestamp || item.created_at || Date.now()).toLocaleString('fr-FR')}</small>
                        </div>
                        <span class="badge bg-secondary font-monospace small">${item.client_op_id ? item.client_op_id.slice(0, 8) + '...' : ''}</span>
                    </div>
                    <p class="mb-2 text-danger fw-semibold small">
                        <i class="fas fa-exclamation-circle me-1"></i> ${item.lastError || item.message || 'Modification concurrente détectée.'}
                    </p>
                    ${adminNotice}
                    ${detailsHtml}
                    <div class="d-flex gap-2 justify-content-end mt-3 flex-wrap">
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-resolve-conflict" data-op-id="${item.client_op_id}" data-action="discard">
                            <i class="fas fa-check me-1"></i> Accepter la version serveur
                        </button>
                        ${forceBtn}
                    </div>
                </div>
            </div>
        `;
    }

    async function refresh() {
        console.log('[SYNC UI] Exécution de refresh()...');
        const online = await updateConnection();

        if (!window.offlineManager || !window.offlineDB) return;

        const allItems = await window.offlineDB.getAllOwnedUnresolved();

        const conflicts = allItems.filter(i => i.status === 'conflict');
        const syncable = allItems.filter(i => i.status === 'pending' || i.status === 'error');

        if (elements.pendingCount) elements.pendingCount.textContent = syncable.length;
        if (elements.totalBadge) elements.totalBadge.textContent = syncable.length;
        if (elements.conflictsMetricCount) elements.conflictsMetricCount.textContent = conflicts.length;
        if (elements.conflictsSectionCount) elements.conflictsSectionCount.textContent = conflicts.length;
        if (elements.lastSync) elements.lastSync.textContent = lastSyncTime;
        if (elements.syncButton) elements.syncButton.disabled = !online || syncable.length === 0 || window.offlineManager.syncInProgress;
        if (elements.clearAllBtn) elements.clearAllBtn.disabled = allItems.length === 0;

        // Affichage de la section Conflits
        if (elements.conflictsSection) {
            if (conflicts.length > 0) {
                elements.conflictsSection.classList.remove('d-none');
                if (elements.conflictsContainer) {
                    elements.conflictsContainer.innerHTML = conflicts.map(renderConflictCard).join('');
                }
            } else {
                elements.conflictsSection.classList.add('d-none');
                if (elements.conflictsContainer) {
                    elements.conflictsContainer.innerHTML = '';
                }
            }
        }

        // Affichage de la liste des opérations en attente de synchronisation
        if (elements.list) {
            if (syncable.length === 0) {
                elements.list.innerHTML = `
                    <div class="sync-empty">
                        <i class="fas fa-check-circle text-success"></i>
                        <span>Aucune donnée en attente de synchronisation.</span>
                    </div>
                `;
                return;
            }

            elements.list.innerHTML = syncable.map((item) => `
                <article class="sync-item">
                    <div>
                        <strong>${formatType(item.type)}</strong>
                        <small>${new Date(item.timestamp || item.created_at || Date.now()).toLocaleString('fr-FR')}</small>
                    </div>
                    <button type="button" class="btn btn-sm btn-light sync-delete" data-id="${item.id}" title="Retirer">
                        <i class="fas fa-trash text-danger"></i>
                    </button>
                </article>
            `).join('');
        }
    }

    async function syncNow() {
        if (!window.offlineManager) return;
        if (elements.syncButton) elements.syncButton.disabled = true;
        const result = await window.offlineManager.sync(true);

        if (result.success) {
            lastSyncTime = new Date().toLocaleString('fr-FR');
            localStorage.setItem('edumanageLastSync', lastSyncTime);
            setAlert(result.message || 'Synchronisation terminée.', 'success');
        } else {
            setAlert(result.message || 'Synchronisation impossible.', 'warning');
        }

        await refresh();
    }

    async function clearAll() {
        if (!window.offlineManager) return;
        if (!window.confirm('Vider toutes les données en attente ?')) return;
        await window.offlineManager.clearAll();
        setAlert('File de synchronisation vidée.', 'success');
        await refresh();
    }

    async function init() {
        if (initialized) return;
        initialized = true;

        console.log('[SYNC UI] Initialisation du module Sync Hors-Ligne...');

        elements.alert = get('syncAlert');
        elements.connectionText = get('connectionText');
        elements.connectionIcon = get('connectionIcon');
        elements.pendingCount = get('pendingCount');
        elements.conflictsMetricCount = get('conflictsMetricCount');
        elements.conflictsSectionCount = get('conflictsSectionCount');
        elements.conflictsSection = get('conflictsSection');
        elements.conflictsContainer = get('conflictsDataContainer');
        elements.lastSync = get('lastSync');
        elements.totalBadge = get('totalBadge');
        elements.syncButton = get('syncButton');
        elements.clearAllBtn = get('clearAllBtn');
        elements.list = get('offlineDataContainer');

        // Mettre à jour immédiatement l'état affiché
        await updateConnection();

        try {
            if (window.offlineManager && typeof window.offlineManager.init === 'function') {
                await window.offlineManager.init();
                window.offlineManager.on('online', refresh);
                window.offlineManager.on('offline', refresh);
                window.offlineManager.on('sync-start', refresh);
                window.offlineManager.on('sync-success', refresh);
                window.offlineManager.on('sync-error', refresh);
                window.offlineManager.on('sync-conflict', refresh);
                window.offlineManager.on('conflict-resolved', refresh);
                window.offlineManager.on('data-deleted', refresh);
                window.offlineManager.on('data-cleared', refresh);
            }

            if (elements.syncButton) elements.syncButton.addEventListener('click', syncNow);
            if (elements.clearAllBtn) elements.clearAllBtn.addEventListener('click', clearAll);

            if (elements.list) {
                elements.list.addEventListener('click', async (event) => {
                    const button = event.target.closest('.sync-delete');
                    if (!button) return;
                    if (window.offlineManager) {
                        await window.offlineManager.deletePending(Number(button.dataset.id));
                    }
                    await refresh();
                });
            }

            if (elements.conflictsContainer) {
                elements.conflictsContainer.addEventListener('click', async (event) => {
                    const button = event.target.closest('.btn-resolve-conflict');
                    if (!button) return;
                    const opId = button.dataset.opId;
                    const action = button.dataset.action;
                    if (!opId || !action) return;

                    button.disabled = true;
                    if (action === 'force') {
                        if (!window.confirm('Confirmer l\'arbitrage ? Votre version écrasera la version existante sur le serveur.')) {
                            button.disabled = false;
                            return;
                        }
                    }

                    try {
                        if (window.offlineManager) {
                            const res = await window.offlineManager.resolveConflict(opId, action);
                            if (res && res.success) {
                                setAlert(res.message || 'Conflit résolu.', 'success');
                            } else {
                                setAlert(res.message || 'Erreur lors de la résolution du conflit.', 'danger');
                            }
                        }
                    } catch (err) {
                        setAlert('Erreur lors de la résolution: ' + err.message, 'danger');
                    }
                    await refresh();
                });
            }

            await refresh();
        } catch (error) {
            console.error('[SYNC UI] Erreur lors de l’init:', error);
            setAlert("La synchronisation hors-ligne n'a pas pu démarrer.", 'danger');
            if (elements.list) {
                elements.list.innerHTML = '<div class="sync-empty"><span>Service indisponible.</span></div>';
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}());
