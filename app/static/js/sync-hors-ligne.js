(function () {
    'use strict';

    const elements = {};
    let initialized = false;
    let lastSyncTime = localStorage.getItem('edumanageLastSync') || 'Jamais';

    function get(id) {
        return document.getElementById(id);
    }

    function setAlert(message, type) {
        const alert = elements.alert;
        if (!alert) return;
        alert.className = `alert alert-${type}`;
        alert.textContent = message;
        alert.classList.remove('d-none');
        window.setTimeout(() => alert.classList.add('d-none'), 4000);
    }

    function formatType(type) {
        const labels = {
            note: 'Note',
            absence: 'Absence',
            paiement: 'Paiement'
        };
        return labels[type] || 'Element';
    }

    function updateConnection() {
        const online = window.offlineManager ? window.offlineManager.isOnline : navigator.onLine;
        elements.connectionText.textContent = online ? 'En ligne' : 'Hors ligne';
        elements.connectionIcon.className = online ? 'sync-status-icon is-online' : 'sync-status-icon is-offline';
        elements.connectionIcon.innerHTML = online
            ? '<i class="fas fa-wifi"></i>'
            : '<i class="fas fa-plug-circle-xmark"></i>';
        return online;
    }

    async function refresh() {
        if (!window.offlineManager) return;

        const online = updateConnection();
        const data = await window.offlineManager.getPendingData();
        const count = data.length;

        elements.pendingCount.textContent = count;
        elements.totalBadge.textContent = count;
        elements.lastSync.textContent = lastSyncTime;
        elements.syncButton.disabled = !online || count === 0 || window.offlineManager.syncInProgress;
        elements.clearAllBtn.disabled = count === 0;

        if (count === 0) {
            elements.list.innerHTML = `
                <div class="sync-empty">
                    <i class="fas fa-check-circle"></i>
                    <span>Aucune donnee en attente.</span>
                </div>
            `;
            return;
        }

        elements.list.innerHTML = data.map((item) => `
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

    async function syncNow() {
        if (!window.offlineManager) return;
        elements.syncButton.disabled = true;
        const result = await window.offlineManager.sync(true);

        if (result.success) {
            lastSyncTime = new Date().toLocaleString('fr-FR');
            localStorage.setItem('edumanageLastSync', lastSyncTime);
            setAlert(result.message || 'Synchronisation terminee.', 'success');
        } else {
            setAlert(result.message || 'Synchronisation impossible.', 'warning');
        }

        await refresh();
    }

    async function clearAll() {
        if (!window.offlineManager) return;
        if (!window.confirm('Vider toutes les donnees en attente ?')) return;
        await window.offlineManager.clearAll();
        setAlert('File de synchronisation videe.', 'success');
        await refresh();
    }

    async function init() {
        if (initialized) return;
        initialized = true;

        elements.alert = get('syncAlert');
        elements.connectionText = get('connectionText');
        elements.connectionIcon = get('connectionIcon');
        elements.pendingCount = get('pendingCount');
        elements.lastSync = get('lastSync');
        elements.totalBadge = get('totalBadge');
        elements.syncButton = get('syncButton');
        elements.clearAllBtn = get('clearAllBtn');
        elements.list = get('offlineDataContainer');

        try {
            await window.offlineManager.init();
            window.offlineManager.on('online', refresh);
            window.offlineManager.on('offline', refresh);
            window.offlineManager.on('sync-start', refresh);
            window.offlineManager.on('sync-success', refresh);
            window.offlineManager.on('sync-error', refresh);
            window.offlineManager.on('data-deleted', refresh);
            window.offlineManager.on('data-cleared', refresh);

            elements.syncButton.addEventListener('click', syncNow);
            elements.clearAllBtn.addEventListener('click', clearAll);
            elements.list.addEventListener('click', async (event) => {
                const button = event.target.closest('.sync-delete');
                if (!button) return;
                await window.offlineManager.deletePending(Number(button.dataset.id));
                await refresh();
            });

            await refresh();
        } catch (error) {
            setAlert("La synchronisation hors-ligne n'a pas pu demarrer.", 'danger');
            elements.list.innerHTML = '<div class="sync-empty"><span>Service indisponible.</span></div>';
        }
    }

    document.addEventListener('DOMContentLoaded', init);
}());
