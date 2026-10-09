// static/js/offline-manager.js - Gestionnaire principal hors-ligne KLASORA V2

class OfflineManager {
    constructor() {
        this.isOnline = navigator.onLine;
        this.syncInProgress = false;
        this.autoSyncEnabled = true;
        this.syncInterval = null;
        this.listeners = new Map();
        this.preloadPromises = new Map();
        this.retryDelay = 5000;
        this.maxRetryDelay = 60000;
        this.preloadFreshnessMs = 15 * 60 * 1000;
        this.adminPreloadTTL = 12 * 60 * 60 * 1000; // 12 heures pour l'annuaire administratif
    }

    /**
     * Initialiser le gestionnaire
     */

    /**
     * Accès sécurisé à localStorage (compatible navigation privée & cookies tiers bloqués)
     */
    _getStorageItem(key) {
        try {
            return (typeof window !== 'undefined' && window.localStorage)
                ? window.localStorage.getItem(key)
                : null;
        } catch (e) {
            return null;
        }
    }

    _setStorageItem(key, value) {
        try {
            if (typeof window !== 'undefined' && window.localStorage) {
                window.localStorage.setItem(key, value);
            }
        } catch (e) {
            // Mode navigation privée ou quota dépassé : ne pas interrompre l'exécution
        }
    }

    _removeStorageItem(key) {
        try {
            if (typeof window !== 'undefined' && window.localStorage) {
                window.localStorage.removeItem(key);
            }
        } catch (e) {
            // Silencieux en navigation privée
        }
    }

    async init() {
        console.log('🎯 Initialisation OfflineManager V2');

        try {
            // Initialiser IndexedDB
            await offlineDB.init();

            // Configurer les écouteurs d'événements
            this.setupEventListeners();

            // Démarrer la synchronisation automatique si en ligne
            this.startAutoSync();

            // Nettoyer les vieux caches
            await offlineDB.cleanExpiredCache();

            // Si professeur connecté, précharger les données pédagogiques
            if (this.isTeacher()) {
                this.preloadTeacherData().catch(e => {
                    console.log('ℹ️ Préchargement professeur différé:', e.message);
                });
            }

            // Si administrateur connecté, précharger les données de l'école
            if (this.isAdmin()) {
                this.preloadAdminData().catch(e => {
                    console.log('ℹ️ Préchargement administrateur différé:', e.message);
                });
            }

            console.log('✅ OfflineManager V3 initialisé');
            this.emit('ready');

        } catch (error) {
            console.error('❌ Erreur initialisation OfflineManager:', error);
            throw error;
        }
    }

    isTeacher() {
        if (typeof window !== 'undefined') {
            if (window.KLASORA_USER && window.KLASORA_USER.role === 'professeur') return true;
            const contextEl = document.getElementById('klasoraUserContext');
            if (contextEl && contextEl.getAttribute('data-user-role') === 'professeur') return true;
        }
        return false;
    }

    isAdmin() {
        if (typeof window !== 'undefined') {
            if (window.KLASORA_USER && window.KLASORA_USER.role === 'admin') return true;
            const contextEl = document.getElementById('klasoraUserContext');
            if (contextEl && contextEl.getAttribute('data-user-role') === 'admin') return true;
        }
        return false;
    }

    isParent() {
        if (typeof window !== 'undefined') {
            if (window.KLASORA_USER && window.KLASORA_USER.role === 'parent') return true;
            const contextEl = document.getElementById('klasoraUserContext');
            if (contextEl && contextEl.getAttribute('data-user-role') === 'parent') return true;
        }
        return false;
    }

    async shouldRefreshPreload(cacheKey, force = false, customTTL = null) {
        if (force) return true;

        const ttl = customTTL || this.preloadFreshnessMs;
        const lastPreload = Number(this._getStorageItem(`offline-preload:${cacheKey}`) || 0);
        if (!lastPreload) return true;

        const cachedData = await offlineDB.getCachedData(cacheKey);
        if (!cachedData) {
            this._removeStorageItem(`offline-preload:${cacheKey}`);
            return true;
        }

        const isFresh = (Date.now() - lastPreload) < ttl;
        if (isFresh) {
            const ageMinutes = Math.round((Date.now() - lastPreload) / 60000);
            console.log(`ℹ️ Préchargement ignoré, cache récent (${cacheKey}, âgé de ${ageMinutes} min)`);
        }
        return !isFresh;
    }

    markPreloadRefreshed(cacheKey) {
        this._setStorageItem(`offline-preload:${cacheKey}`, String(Date.now()));
    }

    async runSinglePreload(cacheKey, force, loader) {
        if (!force && this.preloadPromises.has(cacheKey)) {
            return await this.preloadPromises.get(cacheKey);
        }

        const preloadPromise = loader().finally(() => {
            this.preloadPromises.delete(cacheKey);
        });
        this.preloadPromises.set(cacheKey, preloadPromise);
        return await preloadPromise;
    }

    /**
     * Précharger les données d'administration hors-ligne
     * Conditionné par un cache TTL de 12h (clé localStorage: klasora_offline_admin_last_sync)
     */
    async preloadAdminData(force = false) {
        const cacheKey = offlineDB.getAdminCacheKey();
        return await this.runSinglePreload(cacheKey, force, async () => {
            const ADMIN_SYNC_KEY = 'klasora_offline_admin_last_sync';
            const now = Date.now();

            // 1. Contrôle de fraîcheur via localStorage & IndexedDB si non forcé
            if (!force) {
                const lastSyncStr = this._getStorageItem(ADMIN_SYNC_KEY);
                const lastSync = lastSyncStr ? Number(lastSyncStr) : 0;
                const cachedData = await offlineDB.getCachedData(cacheKey);

                // Si des données valides existent et ont été synchronisées il y a moins de 12 heures
                if (cachedData && lastSync > 0 && (now - lastSync) < this.adminPreloadTTL) {
                    const elapsedMinutes = Math.round((now - lastSync) / 60000);
                    const remainingHours = ((this.adminPreloadTTL - (now - lastSync)) / (3600 * 1000)).toFixed(1);
                    console.log(`ℹ️ [Admin Preload] Cache frais (${elapsedMinutes} min écoulées, expiration dans ~${remainingHours}h). Requête /api/admin/offline-data évitée.`);
                    return cachedData;
                }

                // Si le client est hors-ligne, restituer le cache existant même expiré
                if (!this.isOnline) {
                    return cachedData;
                }
            }

            // 2. Si hors-ligne strict sans forçage
            if (!this.isOnline && !force) {
                return await offlineDB.getCachedData(cacheKey);
            }

            // 3. Récupération distante discrète
            try {
                console.log(`📥 [Admin Preload] Récupération des données hors-ligne (${cacheKey})...${force ? ' (forcé)' : ''}`);
                const response = await fetch('/api/admin/offline-data', {
                    headers: { 'Accept': 'application/json' }
                });

                if (response.ok) {
                    const data = await response.json();
                    if (data.success) {
                        await offlineDB.cacheData(cacheKey, data, 1440); // 24h
                        
                        // Mettre à jour le timestamp TTL de synchronisation (12h)
                        this._setStorageItem(ADMIN_SYNC_KEY, String(Date.now()));
                        this.markPreloadRefreshed(cacheKey);

                        console.log(`✅ Données administrateur préchargées (${data.classes ? data.classes.length : 0} classes, ${data.cours ? data.cours.length : 0} cours, ${data.eleves ? data.eleves.length : 0} élèves)`);
                        this.emit('admin-data-loaded', data);
                        return data;
                    }
                } else if (response.status === 401) {
                    console.warn('⚠️ Session expirée lors du chargement des données administrateur');
                    this.emit('sync-auth-required');
                } else if (response.status === 403) {
                    console.warn('⛔ Accès refusé (403) aux données administrateur');
                }
            } catch (e) {
                console.warn('⚠️ Impossible de rafraîchir les données administrateur (mode hors-ligne):', e.message);
            }

            return await offlineDB.getCachedData(cacheKey);
        });
    }

    /**
     * Forcer l'actualisation manuelle des données administrateur hors-ligne
     * Déclenchable par bouton UI ou script externe
     */
    async refreshAdminOfflineData() {
        console.log('🔄 Actualisation manuelle forcée des données hors-ligne administrateur...');
        return await this.preloadAdminData(true);
    }

    /**
     * Précharger les données pédagogiques hors-ligne du professeur
     */
    async preloadTeacherData(force = false) {
        const cacheKey = offlineDB.getTeacherCacheKey();
        return await this.runSinglePreload(cacheKey, force, async () => {

        if (!this.isOnline && !force) {
            return await offlineDB.getCachedData(cacheKey);
        }

        if (!(await this.shouldRefreshPreload(cacheKey, force))) {
            return await offlineDB.getCachedData(cacheKey);
        }

        try {
            console.log(`📥 Chargement des données hors-ligne du professeur (${cacheKey})...`);
            const response = await fetch('/api/professeur/offline-data', {
                headers: { 'Accept': 'application/json' }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    await offlineDB.cacheData(cacheKey, data, 1440); // 24h
                    this.markPreloadRefreshed(cacheKey);
                    console.log(`✅ Données professeur préchargées (${data.classes.length} classes, ${data.cours.length} cours, ${data.eleves.length} élèves)`);
                    this.emit('teacher-data-loaded', data);
                    return data;
                }
            } else if (response.status === 401) {
                console.warn('⚠️ Session expirée lors du chargement des données professeur');
                this.emit('sync-auth-required');
            } else if (response.status === 403) {
                console.warn('⛔ Accès refusé (403) aux données professeur');
                this.emit('sync-forbidden', { message: "Accès refusé aux données professeur" });
            }
        } catch (e) {
            console.warn('⚠️ Impossible de rafraîchir les données professeur (mode hors-ligne):', e.message);
        }

        return await offlineDB.getCachedData(cacheKey);
        });
    }

    /**
     * Précharger les données de consultation hors-ligne du parent
     */
    async preloadParentData(force = false) {
        const cacheKey = offlineDB.getParentCacheKey();
        return await this.runSinglePreload(cacheKey, force, async () => {

        if (!this.isOnline && !force) {
            return await offlineDB.getCachedData(cacheKey);
        }

        if (!(await this.shouldRefreshPreload(cacheKey, force))) {
            return await offlineDB.getCachedData(cacheKey);
        }

        try {
            console.log(`📥 Chargement des données hors-ligne du parent (${cacheKey})...`);
            const response = await fetch('/api/parent/offline-data', {
                headers: { 'Accept': 'application/json' }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    await offlineDB.cacheData(cacheKey, data, 1440); // 24h
                    this.markPreloadRefreshed(cacheKey);
                    console.log(`✅ Données parent préchargées (${data.enfants ? data.enfants.length : 0} enfants)`);
                    this.emit('parent-data-loaded', data);
                    return data;
                }
            } else if (response.status === 401) {
                console.warn('⚠️ Session expirée lors du chargement des données parent');
                this.emit('sync-auth-required');
            } else if (response.status === 403) {
                console.warn('⛔ Accès refusé (403) aux données parent');
            }
        } catch (e) {
            console.warn('⚠️ Impossible de rafraîchir les données parent (mode hors-ligne):', e.message);
        }

        return await offlineDB.getCachedData(cacheKey);
        });
    }

    /**
     * Teste la connectivité réelle du serveur KLASORA
     */
    async checkServerConnectivity() {
        if (!navigator.onLine) {
            console.log('[SYNC UI] navigator.onLine est false');
            return false;
        }
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 4000);
            const response = await fetch('/api/connectivity', {
                cache: 'no-store',
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            if (!response.ok) {
                console.log('[SYNC UI] Réponse reçue status non-OK:', response.status);
                return false;
            }
            const data = await response.json();
            console.log('[SYNC UI] Réponse reçue:', data);
            const isOnline = !!(data && data.online === true);
            console.log('[SYNC UI] online=true ?', isOnline);
            return isOnline;
        } catch (error) {
            console.log('[SYNC UI] Erreur/Timeout fetch /api/connectivity:', error);
            return false;
        }
    }

    /**
     * Met à jour le statut en ligne de manière asynchrone
     */
    async updateOnlineStatus() {
        const wasOnline = this.isOnline;
        this.isOnline = await this.checkServerConnectivity();
        if (this.isOnline !== wasOnline) {
            if (this.isOnline) {
                console.log('✅ Serveur KLASORA accessible');
                this.retryDelay = 5000;
                this.emit('online');
                this.syncWhenOnline();
                if (this.isTeacher()) this.preloadTeacherData();
                if (this.isAdmin()) this.preloadAdminData();
                if (this.isParent()) this.preloadParentData();
            } else {
                console.log('⚠️ Serveur inaccessible ou coupure réseau');
                this.emit('offline');
            }
        }
        this.emit('status-checked');
        return this.isOnline;
    }

    /**
     * Configurer les écouteurs d'événements
     */
    setupEventListeners() {
        window.addEventListener('online', () => {
            console.log('🌐 navigator.onLine = true, vérification serveur...');
            this.updateOnlineStatus();
        });

        window.addEventListener('offline', () => {
            console.log('📶 Connexion perdue (navigator.onLine = false)');
            this.isOnline = false;
            this.emit('offline');
        });

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && navigator.onLine) {
                this.updateOnlineStatus();
            }
        });

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.addEventListener('message', (event) => {
                if (event.data && event.data.type === 'TRIGGER_SYNC') {
                    console.log('📬 Demande de synchronisation reçue du Service Worker');
                    this.sync();
                }
            });
        }
    }

    /**
     * Ajouter une opération à la queue hors-ligne
     */
    async addToSync(type, data) {
        try {
            const syncData = {
                type,
                ...data,
                created_at: new Date().toISOString()
            };

            const res = await offlineDB.addPendingSync(syncData);
            console.log(`✅ ${type} ajouté localement (op_id: ${res.client_op_id})`);

            // Mettre à jour l'UI locale immédiatement
            this.emit('data-added', { type, client_op_id: res.client_op_id });

            // Essayer de synchroniser immédiatement si la connexion est active
            if (navigator.onLine) {
                setTimeout(() => this.sync(), 300);
            }

            return res;

        } catch (error) {
            console.error('❌ Erreur ajout opération locale:', error);
            throw error;
        }
    }

    async getFreshCsrfToken() {
        const response = await fetch('/api/csrf-token', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            cache: 'no-store',
            credentials: 'same-origin'
        });

        if (response.status === 401 || response.status === 403) {
            this.emit('sync-auth-required');
            return null;
        }

        if (!response.ok) {
            throw new Error(`Impossible de rafraichir le token CSRF (HTTP ${response.status})`);
        }

        const data = await response.json();
        const token = data && data.csrf_token;
        if (!token) {
            throw new Error('Token CSRF frais manquant');
        }

        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) {
            meta.setAttribute('content', token);
        }
        return token;
    }

    /**
     * Synchroniser les données avec le serveur (idempotence + réponse granulaire)
     */
    async sync(force = false) {
        if (this.syncInProgress && !force) {
            console.log('⏳ Synchronisation déjà en cours');
            return { success: false, message: 'Synchronisation en cours' };
        }

        if (!navigator.onLine) {
            console.log('📡 Hors ligne - synchronisation différée');
            return { success: false, message: 'Hors ligne' };
        }

        this.syncInProgress = true;
        this.emit('sync-start');

        try {
            const pendingData = await offlineDB.getSyncablePending();
            
            if (!pendingData || pendingData.length === 0) {
                console.log('ℹ️ Aucune opération en attente');
                this.syncInProgress = false;
                this.emit('sync-end');
                return { success: true, message: 'Aucune donnée', count: 0 };
            }

            console.log(`🚀 Tentative de synchronisation de ${pendingData.length} opération(s)...`);

            const BATCH_SIZE = 50;
            let confirmedCount = 0;
            let conflictCount = 0;
            let errorCount = 0;
            let lastMessage = '';

            const csrfToken = await this.getFreshCsrfToken();
            if (!csrfToken) {
                return { success: false, message: 'Session expirÃ©e' };
            }

            // Traitement par lots de 50 pour éviter les saturations et sécuriser la progression
            for (let i = 0; i < pendingData.length; i += BATCH_SIZE) {
                const batch = pendingData.slice(i, i + BATCH_SIZE);
                const payload = batch.map(item => {
                    const { id, synced, syncedAt, ...rest } = item;
                    return rest;
                });

                let response;
                try {
                    response = await fetch('/api/sync', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
                        },
                        body: JSON.stringify(payload)
                    });
                } catch (netErr) {
                    // Coupure réseau : marquer localement comme hors ligne
                    this.isOnline = false;
                    throw new Error(`Coupure réseau pendant l'envoi du lot: ${netErr.message}`);
                }

                if (response.status === 401) {
                    console.warn('⚠️ Session expirée ou non authentifiée (HTTP 401). File d\'attente conservée intacte.');
                    this.emit('sync-auth-required');
                    return { success: false, message: 'Session expirée' };
                }

                if (response.status === 403) {
                    console.warn('⛔ Accès interdit (HTTP 403) : autorisation révoquée ou périmètre interdit.');
                    const forbiddenMsg = "Vous n'avez plus l'autorisation de modifier cette donnée.";
                    for (const item of batch) {
                        const opId = item.client_op_id;
                        if (opId) {
                            await offlineDB.updatePendingStatus(opId, 'forbidden', forbiddenMsg);
                        }
                    }
                    this.emit('sync-forbidden', { message: forbiddenMsg, batch });
                    // Ne pas retry en boucle : les éléments sont marqués 'forbidden' et exclus du sync automatique
                    return { success: false, message: forbiddenMsg, forbidden: true };
                }

                if (!response.ok) {
                    throw new Error(`Erreur serveur HTTP ${response.status}`);
                }

                const result = await response.json();
                console.log(`📥 Réponse synchronisation lot ${Math.floor(i / BATCH_SIZE) + 1}:`, result);
                lastMessage = result.message || lastMessage;

                if (result && Array.isArray(result.results)) {
                    for (const r of result.results) {
                        const opId = r.client_op_id;
                        if (!opId) continue;

                        if (r.status === 'synced' || r.status === 'already_processed') {
                            // Le serveur a validé ou avait déjà validé : supprimer immédiatement de la file locale
                            await offlineDB.deletePendingByClientOpId(opId);
                            confirmedCount++;
                        } else if (r.status === 'conflict') {
                            // Conflit détecté : marquer localement sans supprimer
                            await offlineDB.updatePendingStatus(opId, 'conflict', r.message, {
                                server_value: r.server_value,
                                client_value: r.client_value,
                                locked_by_admin: r.locked_by_admin,
                                can_arbitrate: r.can_arbitrate,
                                entity: r.entity,
                                entity_id: r.entity_id
                            });
                            conflictCount++;
                            this.emit('sync-conflict', { client_op_id: opId, message: r.message, ...r });
                        } else if (r.status === 'forbidden') {
                            // Accès refusé par le serveur
                            const forbiddenMsg = r.message || "Vous n'avez plus l'autorisation de modifier cette donnée.";
                            await offlineDB.updatePendingStatus(opId, 'forbidden', forbiddenMsg);
                            this.emit('sync-forbidden', { client_op_id: opId, message: forbiddenMsg, ...r });
                            errorCount++;
                        } else {
                            // Autre erreur
                            await offlineDB.updatePendingStatus(opId, 'error', r.message);
                            errorCount++;
                        }
                    }
                } else if (result.success && result.processed > 0) {
                    for (const item of batch) {
                        await offlineDB.deletePending(item.id);
                        confirmedCount++;
                    }
                }
            }

            await offlineDB.addSyncLog('success', lastMessage || 'Synchronisation terminée', {
                confirmed: confirmedCount,
                conflicts: conflictCount,
                errors: errorCount
            });

            this.emit('sync-success', {
                count: confirmedCount,
                conflicts: conflictCount,
                errors: errorCount,
                message: lastMessage
            });

            // Réinitialiser le délai de retry si succès
            this.retryDelay = 5000;

            return {
                success: true,
                count: confirmedCount,
                conflicts: conflictCount,
                errors: errorCount,
                message: lastMessage
            };

        } catch (error) {
            console.error('❌ Erreur lors de la synchronisation:', error);

            await offlineDB.addSyncLog('error', error.message, {
                timestamp: Date.now()
            });

            this.emit('sync-error', { error: error.message });

            // Backoff exponentiel
            this.retryDelay = Math.min(this.retryDelay * 2, this.maxRetryDelay);
            console.log(`⏱️ Prochain essai dans ${this.retryDelay / 1000}s`);

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
     * Synchroniser automatiquement au retour du réseau
     */
    async syncWhenOnline() {
        if (!navigator.onLine) return;

        setTimeout(async () => {
            const syncable = await offlineDB.getSyncablePending();
            if (syncable && syncable.length > 0) {
                await this.sync();
            }
        }, 1500);
    }

    /**
     * Démarrer l'auto-synchronisation périodique
     */
    startAutoSync(intervalMinutes = 3) {
        if (this.syncInterval) {
            clearInterval(this.syncInterval);
        }

        if (!this.autoSyncEnabled) return;

        this.syncInterval = setInterval(async () => {
            if (navigator.onLine && !this.syncInProgress) {
                const syncable = await offlineDB.getSyncablePending();
                if (syncable && syncable.length > 0) {
                    await this.sync();
                }
            }
        }, intervalMinutes * 60 * 1000);
    }

    /**
     * Purger le cache pédagogique lors de la déconnexion (téléphone partagé)
     * NE TOUCHE PAS au store pendingSync !
     */
    async cleanTeacherDataOnLogout() {
        try {
            await offlineDB.clearTeacherOfflineData();
            console.log('🧹 Cache pédagogique professeur purgé avec succès (pendingSync préservé)');
        } catch (e) {
            console.warn('⚠️ Erreur nettoyage cache professeur logout:', e);
        }
    }

    /**
     * Purger le cache d'administration lors de la déconnexion (ordinateur partagé)
     * NE TOUCHE PAS au store pendingSync !
     */
    async cleanAdminDataOnLogout() {
        try {
            await offlineDB.clearAdminOfflineData();
            console.log('🧹 Cache administration purgé avec succès (pendingSync préservé)');
        } catch (e) {
            console.warn('⚠️ Erreur nettoyage cache administration logout:', e);
        }
    }

    /**
     * Purge globale de toutes les données privées lors de la déconnexion
     */
    async cleanAllUserDataOnLogout() {
        try {
            await offlineDB.clearAllUserPrivateData();
            console.log('🧹 Données privées de l\'utilisateur purgées au logout');
        } catch (e) {
            console.warn('⚠️ Erreur nettoyage données privées logout:', e);
        }
    }

    /**
     * Résoudre un conflit local
     * @param {string} clientOpId
     * @param {'discard'|'force'} action
     */
    async resolveConflict(clientOpId, action) {
        if (!clientOpId) return { success: false, message: 'client_op_id manquant' };

        if (action === 'discard') {
            // Accepter la version serveur : supprimer la modification locale en conflit
            await offlineDB.deletePendingByClientOpId(clientOpId);
            this.emit('conflict-resolved', { client_op_id: clientOpId, action: 'discard' });
            return { success: true, action: 'discard', message: 'Version serveur acceptée (brouillon local supprimé)' };
        } else if (action === 'force') {
            // Forcer la version locale (arbitrage Admin)
            const items = await offlineDB.getAllOwnedUnresolved();
            const item = items.find(d => d.client_op_id === clientOpId);
            if (item) {
                item.force = true;
                item.status = 'pending';
                item.retries = 0;
                const tx = offlineDB.db.transaction(['pendingSync'], 'readwrite');
                const store = tx.objectStore('pendingSync');
                store.put(item);
                await new Promise(resolve => { tx.oncomplete = resolve; });
                this.emit('conflict-resolved', { client_op_id: clientOpId, action: 'force' });
                // Lancer la synchronisation immédiate pour appliquer l'arbitrage
                return await this.sync(true);
            }
            return { success: false, message: 'Opération introuvable' };
        }
        return { success: false, message: 'Action inconnue' };
    }

    stopAutoSync() {
        if (this.syncInterval) {
            clearInterval(this.syncInterval);
            this.syncInterval = null;
        }
    }

    async getStats() {
        return await offlineDB.getStats();
    }

    async getPendingData() {
        return await offlineDB.getAllPending();
    }

    async deletePending(id) {
        await offlineDB.deletePending(id);
        this.emit('data-deleted', { id });
    }

    async clearAll(userId = null, ecoleId = null) {
        await offlineDB.viderFileUtilisateurCourant(userId, ecoleId);
        this.emit('data-cleared');
    }

    async viderFileUtilisateurCourant(userId = null, ecoleId = null) {
        return await this.clearAll(userId, ecoleId);
    }

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
        if (index > -1) callbacks.splice(index, 1);
    }

    emit(event, data) {
        if (!this.listeners.has(event)) return;
        this.listeners.get(event).forEach(cb => {
            try { cb(data); } catch (e) { console.error(`Erreur listener ${event}:`, e); }
        });
    }

    getStatus() {
        return {
            isOnline: navigator.onLine,
            syncInProgress: this.syncInProgress,
            autoSyncEnabled: this.autoSyncEnabled
        };
    }
}

// Instance singleton
const offlineManager = new OfflineManager();

if (typeof window !== 'undefined') {
    window.offlineManager = offlineManager;
    document.addEventListener('DOMContentLoaded', () => {
        offlineManager.init().catch(err => {
            console.error('❌ Échec init OfflineManager:', err);
        });
    });
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = offlineManager;
}
