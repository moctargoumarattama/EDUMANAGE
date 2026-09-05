// static/js/offline-manager.js - Gestionnaire principal hors-ligne
class OfflineManager {
    constructor() {
        this.isOnline = navigator.onLine;
        this.syncInProgress = false;
        this.autoSyncEnabled = true;
        this.syncInterval = null;
        this.listeners = new Map();
    }

    /**
     * Initialiser le gestionnaire
     */
    async init() {
        console.log('🎯 Initialisation OfflineManager');

        try {
            // Initialiser IndexedDB
            await offlineDB.init();

            // Enregistrer le Service Worker
            await this.registerServiceWorker();

            // Configurer les écouteurs d'événements
            this.setupEventListeners();

            // Démarrer la synchronisation automatique
            this.startAutoSync();

            // Nettoyer les vieux caches
            await offlineDB.cleanExpiredCache();

            console.log('✅ OfflineManager initialisé');
            this.emit('ready');

        } catch (error) {
            console.error('❌ Erreur initialisation OfflineManager:', error);
            throw error;
        }
    }

    /**
     * Enregistrer le Service Worker
     */
    async registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            console.warn('⚠️ Service Worker non supporté');
            return;
        }

        try {
            const registration = await navigator.serviceWorker.register('/service-worker.js', {
                scope: '/'
            });

            console.log('✅ Service Worker enregistré:', registration.scope);

            // Gérer les mises à jour
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;
                console.log('🔄 Nouvelle version du Service Worker disponible');

                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        console.log('✨ Mise à jour disponible - Rechargez la page');
                        this.emit('update-available');
                    }
                });
            });

            // Écouter les messages du Service Worker
            navigator.serviceWorker.addEventListener('message', event => {
                this.handleServiceWorkerMessage(event.data);
            });

            // Enregistrer la synchronisation en arrière-plan
            if ('sync' in registration) {
                console.log('✅ Background Sync disponible');
            }

        } catch (error) {
            console.error('❌ Erreur enregistrement Service Worker:', error);
        }
    }

    /**
     * Configurer les écouteurs d'événements
     */
    setupEventListeners() {
        // Détecter les changements de connexion
        window.addEventListener('online', () => {
            console.log('🌐 Connexion rétablie');
            this.isOnline = true;
            this.emit('online');
            this.syncWhenOnline();
        });

        window.addEventListener('offline', () => {
            console.log('📡 Connexion perdue');
            this.isOnline = false;
            this.emit('offline');
        });

        // Synchroniser avant de quitter la page
        window.addEventListener('beforeunload', () => {
            if (!this.isOnline) return;
            
            // Tenter une synchronisation rapide
            navigator.sendBeacon && this.trySendBeacon();
        });

        // Synchroniser quand la page redevient visible
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && this.isOnline) {
                this.syncWhenOnline();
            }
        });
    }

    /**
     * Gérer les messages du Service Worker
     */
    handleServiceWorkerMessage(data) {
        console.log('📨 Message Service Worker:', data);

        switch (data.type) {
            case 'SYNC_SUCCESS':
                this.emit('sync-success', { count: data.count });
                break;
            case 'SYNC_ERROR':
                this.emit('sync-error', { error: data.error });
                break;
        }
    }

    /**
     * Ajouter des données à synchroniser
     */
    async addToSync(type, data) {
        try {
            const syncData = {
                type,
                ...data,
                created_at: new Date().toISOString()
            };

            const id = await offlineDB.addPendingSync(syncData);
            console.log(`✅ ${type} ajouté à la queue (ID: ${id})`);

            // Enregistrer la synchronisation en arrière-plan si disponible
            this.registerBackgroundSync();

            // Essayer de synchroniser immédiatement si en ligne
            if (this.isOnline) {
                setTimeout(() => this.sync(), 1000);
            }

            this.emit('data-added', { type, id });
            return id;

        } catch (error) {
            console.error('❌ Erreur ajout donnée:', error);
            throw error;
        }
    }

    /**
     * Enregistrer une synchronisation en arrière-plan
     */
    async registerBackgroundSync() {
        if (!('serviceWorker' in navigator) || !('sync' in ServiceWorkerRegistration.prototype)) {
            return;
        }

        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.sync.register('sync-data');
            console.log('✅ Background Sync enregistré');
        } catch (error) {
            console.warn('⚠️ Background Sync non disponible:', error);
        }
    }

    /**
     * Synchroniser les données
     */
    async sync(force = false) {
        if (this.syncInProgress && !force) {
            console.log('⏳ Synchronisation déjà en cours');
            return { success: false, message: 'Synchronisation en cours' };
        }

        if (!this.isOnline) {
            console.log('📡 Hors ligne - synchronisation reportée');
            return { success: false, message: 'Hors ligne' };
        }

        this.syncInProgress = true;
        this.emit('sync-start');

        try {
            const pendingData = await offlineDB.getAllPending();
            
            if (pendingData.length === 0) {
                console.log('ℹ️ Aucune donnée à synchroniser');
                this.syncInProgress = false;
                return { success: true, message: 'Aucune donnée', count: 0 };
            }

            console.log(`🚀 Synchronisation de ${pendingData.length} élément(s)`);

            // Préparer les données (retirer les champs internes)
            const dataToSync = pendingData.map(item => {
                const { id, timestamp, synced, syncedAt, retries, ...data } = item;
                return data;
            });

            // Envoyer au serveur
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
            const response = await fetch('/api/sync', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
                },
                body: JSON.stringify(dataToSync)
            });

            if (!response.ok) {
                throw new Error(`Erreur ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            console.log('✅ Synchronisation réussie:', result);

            if (result.success) {
                // Supprimer les données synchronisées
                for (const item of pendingData) {
                    await offlineDB.deletePending(item.id);
                }

                // Ajouter un log
                await offlineDB.addSyncLog('success', result.message, {
                    count: pendingData.length,
                    processed: result.processed
                });

                this.emit('sync-success', {
                    count: pendingData.length,
                    message: result.message
                });

                return {
                    success: true,
                    count: pendingData.length,
                    message: result.message,
                    errors: result.errors
                };
            } else {
                throw new Error(result.message || 'Erreur de synchronisation');
            }

        } catch (error) {
            console.error('❌ Erreur synchronisation:', error);

            await offlineDB.addSyncLog('error', error.message, {
                timestamp: Date.now()
            });

            this.emit('sync-error', { error: error.message });

            return {
                success: false,
                message: error.message
            };

        } finally {
            this.syncInProgress = false;
            this.emit('sync-end');
        }
    }

    /**
     * Synchroniser quand la connexion revient
     */
    async syncWhenOnline() {
        if (!this.isOnline) return;

        console.log('🔄 Tentative de synchronisation automatique');
        
        // Attendre un peu pour laisser la connexion se stabiliser
        setTimeout(async () => {
            const stats = await offlineDB.getStats();
            if (stats.total > 0) {
                await this.sync();
            }
        }, 2000);
    }

    /**
     * Démarrer la synchronisation automatique périodique
     */
    startAutoSync(intervalMinutes = 5) {
        if (this.syncInterval) {
            clearInterval(this.syncInterval);
        }

        if (!this.autoSyncEnabled) return;

        this.syncInterval = setInterval(async () => {
            if (this.isOnline && !this.syncInProgress) {
                console.log('⏰ Synchronisation automatique périodique');
                const stats = await offlineDB.getStats();
                if (stats.total > 0) {
                    await this.sync();
                }
            }
        }, intervalMinutes * 60 * 1000);

        console.log(`⏰ Synchronisation automatique activée (${intervalMinutes}min)`);
    }

    /**
     * Arrêter la synchronisation automatique
     */
    stopAutoSync() {
        if (this.syncInterval) {
            clearInterval(this.syncInterval);
            this.syncInterval = null;
            console.log('⏸️ Synchronisation automatique arrêtée');
        }
    }

    /**
     * Essayer d'envoyer avec sendBeacon (pour beforeunload)
     */
    async trySendBeacon() {
        try {
            const pendingData = await offlineDB.getAllPending();
            if (pendingData.length === 0) return;

            const blob = new Blob([JSON.stringify(pendingData)], {
                type: 'application/json'
            });

            navigator.sendBeacon('/api/sync', blob);
            console.log('📤 Données envoyées via sendBeacon');

        } catch (error) {
            console.error('❌ Erreur sendBeacon:', error);
        }
    }

    /**
     * Obtenir les statistiques
     */
    async getStats() {
        return await offlineDB.getStats();
    }

    /**
     * Obtenir toutes les données en attente
     */
    async getPendingData() {
        return await offlineDB.getAllPending();
    }

    /**
     * Supprimer une donnée en attente
     */
    async deletePending(id) {
        await offlineDB.deletePending(id);
        this.emit('data-deleted', { id });
    }

    /**
     * Vider toutes les données en attente
     */
    async clearAll() {
        await offlineDB.clearAllPending();
        this.emit('data-cleared');
    }

    /**
     * Système d'événements
     */
    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
    }

    off(event, callback) {
        if (!this.listeners.has(event)) return;
        const callbacks = this.listeners.get(event);
        const index = callbacks.indexOf(callback);
        if (index > -1) {
            callbacks.splice(index, 1);
        }
    }

    emit(event, data) {
        if (!this.listeners.has(event)) return;
        this.listeners.get(event).forEach(callback => {
            try {
                callback(data);
            } catch (error) {
                console.error(`❌ Erreur callback ${event}:`, error);
            }
        });
    }

    /**
     * Vérifier l'état
     */
    getStatus() {
        return {
            isOnline: this.isOnline,
            syncInProgress: this.syncInProgress,
            autoSyncEnabled: this.autoSyncEnabled,
            serviceWorkerActive: navigator.serviceWorker?.controller !== null
        };
    }
}

// Instance singleton
const offlineManager = new OfflineManager();

// Export
if (typeof window !== 'undefined') {
    window.offlineManager = offlineManager;
}
