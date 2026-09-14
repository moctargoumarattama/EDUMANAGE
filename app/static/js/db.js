// static/js/db.js - Gestionnaire IndexedDB KLASORA V2 (Multi-utilisateurs & Idempotence)

function generateUUID() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
        return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
            (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
        );
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

class OfflineDB {
    constructor() {
        this.dbName = 'EcoleDB';
        this.version = 2; // V2: Ajout client_op_id, user_id, status
        this.db = null;
    }

    /**
     * Initialiser la base de données
     */
    async init() {
        if (this.db) return this.db;

        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.version);

            request.onerror = () => {
                console.error('❌ Erreur ouverture IndexedDB:', request.error);
                reject(request.error);
            };

            request.onsuccess = () => {
                this.db = request.result;
                console.log('✅ IndexedDB V2 initialisée');
                // Migration non destructive : purger uniquement l'ancien cache global 'teacher_offline_data'
                // NE JAMAIS toucher à pendingSync ni appeler deleteDatabase()
                try {
                    const tx = this.db.transaction(['cachedData'], 'readwrite');
                    const store = tx.objectStore('cachedData');
                    store.delete('teacher_offline_data');
                } catch (e) {
                    // Ignore si cachedData n'est pas encore prêt ou clé inexistante
                }
                resolve(this.db);
            };

            request.onupgradeneeded = (event) => {
                console.log('🔧 Migration/Initialisation de la structure IndexedDB V2');
                const db = event.target.result;
                const transaction = event.target.transaction;

                // 1. Store pour les données en attente de synchronisation
                let syncStore;
                if (!db.objectStoreNames.contains('pendingSync')) {
                    syncStore = db.createObjectStore('pendingSync', {
                        keyPath: 'id',
                        autoIncrement: true
                    });
                    syncStore.createIndex('type', 'type', { unique: false });
                    syncStore.createIndex('timestamp', 'timestamp', { unique: false });
                    syncStore.createIndex('synced', 'synced', { unique: false });
                    syncStore.createIndex('client_op_id', 'client_op_id', { unique: false });
                    syncStore.createIndex('user_id', 'user_id', { unique: false });
                    syncStore.createIndex('status', 'status', { unique: false });
                } else {
                    syncStore = transaction.objectStore('pendingSync');
                    if (!syncStore.indexNames.contains('client_op_id')) {
                        syncStore.createIndex('client_op_id', 'client_op_id', { unique: false });
                    }
                    if (!syncStore.indexNames.contains('user_id')) {
                        syncStore.createIndex('user_id', 'user_id', { unique: false });
                    }
                    if (!syncStore.indexNames.contains('status')) {
                        syncStore.createIndex('status', 'status', { unique: false });
                    }
                }

                // 2. Store pour le cache de données
                if (!db.objectStoreNames.contains('cachedData')) {
                    const cacheStore = db.createObjectStore('cachedData', { 
                        keyPath: 'key' 
                    });
                    cacheStore.createIndex('expiry', 'expiry', { unique: false });
                }

                // 3. Store pour les logs
                if (!db.objectStoreNames.contains('syncLogs')) {
                    const logStore = db.createObjectStore('syncLogs', { 
                        keyPath: 'id', 
                        autoIncrement: true 
                    });
                    logStore.createIndex('timestamp', 'timestamp', { unique: false });
                    logStore.createIndex('status', 'status', { unique: false });
                }
            };
        });
    }

    getCurrentUserId() {
        if (typeof window !== 'undefined') {
            if (window.KLASORA_USER && window.KLASORA_USER.id) {
                return window.KLASORA_USER.id;
            }
            const contextEl = document.getElementById('klasoraUserContext');
            if (contextEl) {
                const uid = contextEl.getAttribute('data-user-id');
                if (uid) return parseInt(uid, 10);
            }
        }
        return null;
    }

    getCurrentEcoleId() {
        if (typeof window !== 'undefined') {
            if (window.KLASORA_USER) {
                if (window.KLASORA_USER.ecole_id) return window.KLASORA_USER.ecole_id;
                if (window.KLASORA_USER.ecoleId) return window.KLASORA_USER.ecoleId;
            }
            const contextEl = document.getElementById('klasoraUserContext');
            if (contextEl) {
                const eid = contextEl.getAttribute('data-ecole-id');
                if (eid) return parseInt(eid, 10);
            }
        }
        return null;
    }

    /**
     * Générateur centralisé de la clé de cache des données professeur
     * Format: teacher_offline_data:<ecole_id>:<user_id>
     */
    getTeacherCacheKey(ecoleId = null, userId = null) {
        const eId = ecoleId !== null ? ecoleId : this.getCurrentEcoleId();
        const uId = userId !== null ? userId : this.getCurrentUserId();
        return `teacher_offline_data:${eId || 'no_ecole'}:${uId || 'no_user'}`;
    }

    /**
     * Générateur centralisé de la clé de cache des données administrateur
     * Format: admin_offline_data:<ecole_id>:<user_id>
     */
    getAdminCacheKey(ecoleId = null, userId = null) {
        const eId = ecoleId !== null ? ecoleId : this.getCurrentEcoleId();
        const uId = userId !== null ? userId : this.getCurrentUserId();
        return `admin_offline_data:${eId || 'no_ecole'}:${uId || 'no_user'}`;
    }

    /**
     * Générateur centralisé de la clé de cache des données parent
     * Format: parent_offline_data:<ecole_id>:<user_id>
     */
    getParentCacheKey(ecoleId = null, userId = null) {
        const eId = ecoleId !== null ? ecoleId : this.getCurrentEcoleId();
        const uId = userId !== null ? userId : this.getCurrentUserId();
        return `parent_offline_data:${eId || 'no_ecole'}:${uId || 'no_user'}`;
    }

    /**
     * Ajouter une donnée en attente de synchronisation
     * Génère client_op_id de manière unique et immuable dès la création
     */
    async addPendingSync(data) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');

            const clientOpId = data.client_op_id || generateUUID();
            const currentUserId = data.user_id !== undefined ? data.user_id : this.getCurrentUserId();
            const currentEcoleId = data.ecole_id !== undefined ? data.ecole_id : this.getCurrentEcoleId();

            const dataToStore = {
                ...data,
                client_op_id: clientOpId,
                user_id: currentUserId,
                ecole_id: currentEcoleId,
                status: data.status || 'pending', // 'pending', 'syncing', 'conflict', 'forbidden', 'error'
                timestamp: Date.now(),
                synced: false,
                retries: 0
            };

            const request = store.add(dataToStore);

            request.onsuccess = () => {
                console.log(`✅ Opération enregistrée localement (client_op_id: ${clientOpId})`);
                resolve({ id: request.result, client_op_id: clientOpId });
            };

            request.onerror = () => {
                console.error('❌ Erreur ajout donnée:', request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Récupérer STRICTEMENT les opérations synchronisables pour l'utilisateur actif
     * Fail-closed: item.user_id === activeUserId obligatoire, exclut les conflits et forbidden
     */
    async getSyncablePending(userId = null, ecoleId = null) {
        if (!this.db) await this.init();

        const targetUserId = userId !== null ? userId : this.getCurrentUserId();
        const targetEcoleId = ecoleId !== null ? ecoleId : this.getCurrentEcoleId();

        if (!targetUserId) {
            console.warn('🔒 Aucun utilisateur actif : synchronisation bloquée (fail-closed)');
            return [];
        }

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readonly');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const all = request.result || [];
                const syncable = all.filter(item => {
                    if (item.synced) return false;
                    // Statut synchronisable uniquement ('pending' ou 'error')
                    if (item.status !== 'pending' && item.status !== 'error') return false;
                    // Fail-closed : propriétaire strict
                    if (item.user_id === null || item.user_id === undefined || item.user_id !== targetUserId) {
                        return false;
                    }
                    if (targetEcoleId && item.ecole_id && item.ecole_id !== targetEcoleId) {
                        return false;
                    }
                    return true;
                });
                resolve(syncable);
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Récupérer toutes les opérations non synchronisées de l'utilisateur actif
     * Inclut: pending, error, forbidden, conflict
     */
    async getAllOwnedUnresolved(userId = null, ecoleId = null) {
        if (!this.db) await this.init();

        const targetUserId = userId !== null ? userId : this.getCurrentUserId();
        const targetEcoleId = ecoleId !== null ? ecoleId : this.getCurrentEcoleId();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readonly');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const all = request.result || [];
                const owned = all.filter(item => {
                    if (item.synced) return false;
                    if (targetUserId) {
                        if (item.user_id !== targetUserId) return false;
                    }
                    if (targetEcoleId && item.ecole_id) {
                        if (item.ecole_id !== targetEcoleId) return false;
                    }
                    return true;
                });
                resolve(owned);
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Récupérer toutes les données en attente (rétro-compatibilité)
     */
    async getAllPending(userId = null) {
        return this.getAllOwnedUnresolved(userId);
    }

    /**
     * Récupérer les données par type
     */
    async getPendingByType(type, userId = null) {
        const all = await this.getAllPending(userId);
        return all.filter(item => item.type === type);
    }

    /**
     * Mettre à jour le statut d'une opération par son client_op_id
     */
    async updatePendingStatus(client_op_id, status, errorMsg = null, extraData = null) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const items = request.result || [];
                const item = items.find(d => d.client_op_id === client_op_id);
                if (item) {
                    item.status = status;
                    if (errorMsg) item.lastError = errorMsg;
                    if (extraData && typeof extraData === 'object') {
                        Object.assign(item, extraData);
                    }
                    if (status === 'error') item.retries = (item.retries || 0) + 1;
                    store.put(item);
                }
                resolve(item);
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Supprimer une opération par son client_op_id (après confirmation serveur)
     */
    async deletePendingByClientOpId(client_op_id) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const items = request.result || [];
                const item = items.find(d => d.client_op_id === client_op_id);
                if (item) {
                    store.delete(item.id);
                    console.log(`🗑️ Opération supprimée après sync confirmée (${client_op_id})`);
                }
                resolve();
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Marquer une donnée comme synchronisée
     */
    async markAsSynced(id) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.get(id);

            request.onsuccess = () => {
                const data = request.result;
                if (data) {
                    data.synced = true;
                    data.status = 'synced';
                    data.syncedAt = Date.now();
                    store.put(data);
                }
                resolve();
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Supprimer une donnée
     */
    async deletePending(id) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.delete(id);

            request.onsuccess = () => {
                console.log('🗑️ Donnée supprimée:', id);
                resolve();
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Vider toutes les données synchronisées
     */
    async clearSynced() {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const items = request.result || [];
                const synced = items.filter(item => item.synced || item.status === 'synced');
                for (const item of synced) {
                    store.delete(item.id);
                }
                resolve(synced.length);
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Vider toutes les données en attente
     */
    async clearAllPending() {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');
            const request = store.clear();

            request.onsuccess = () => {
                console.log('🗑️ Toutes les données en attente supprimées');
                resolve();
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Obtenir des statistiques sur les éléments locaux non résolus
     */
    async getStats(userId = null, ecoleId = null) {
        if (!this.db) await this.init();

        const allData = await this.getAllOwnedUnresolved(userId, ecoleId);
        
        const stats = {
            total: allData.length,
            pending: allData.filter(d => d.status === 'pending').length,
            notes: allData.filter(d => d.type === 'note').length,
            absences: allData.filter(d => d.type === 'absence').length,
            paiements: allData.filter(d => d.type === 'paiement').length,
            conflicts: allData.filter(d => d.status === 'conflict').length,
            errors: allData.filter(d => d.status === 'error' || d.status === 'forbidden').length,
            oldestTimestamp: allData.length > 0 ? Math.min(...allData.map(d => d.timestamp)) : null
        };

        return stats;
    }

    /**
     * Ajouter un log de synchronisation
     */
    async addSyncLog(status, message, details = {}) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['syncLogs'], 'readwrite');
            const store = tx.objectStore('syncLogs');

            const log = {
                timestamp: Date.now(),
                status,
                message,
                details
            };

            const request = store.add(log);

            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Récupérer les logs récents
     */
    async getRecentLogs(limit = 50) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['syncLogs'], 'readonly');
            const store = tx.objectStore('syncLogs');
            const index = store.index('timestamp');
            const request = index.openCursor(null, 'prev');

            const logs = [];
            request.onsuccess = (event) => {
                const cursor = event.target.result;
                if (cursor && logs.length < limit) {
                    logs.push(cursor.value);
                    cursor.continue();
                } else {
                    resolve(logs);
                }
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Mettre en cache des données
     */
    async cacheData(key, data, expiryMinutes = 60) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readwrite');
            const store = tx.objectStore('cachedData');

            const cacheEntry = {
                key,
                data,
                timestamp: Date.now(),
                expiry: Date.now() + (expiryMinutes * 60 * 1000)
            };

            const request = store.put(cacheEntry);

            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Récupérer des données du cache
     */
    async getCachedData(key) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readonly');
            const store = tx.objectStore('cachedData');
            const request = store.get(key);

            request.onsuccess = () => {
                const entry = request.result;
                if (!entry) {
                    resolve(null);
                    return;
                }

                if (entry.expiry < Date.now()) {
                    console.log('⏱️ Cache expiré pour:', key);
                    resolve(null);
                    return;
                }

                resolve(entry.data);
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Nettoyer les caches expirés
     */
    async cleanExpiredCache() {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readwrite');
            const store = tx.objectStore('cachedData');
            const index = store.index('expiry');
            const range = IDBKeyRange.upperBound(Date.now());
            const request = index.openCursor(range);

            let deletedCount = 0;

            request.onsuccess = (event) => {
                const cursor = event.target.result;
                if (cursor) {
                    cursor.delete();
                    deletedCount++;
                    cursor.continue();
                } else {
                    console.log(`🗑️ ${deletedCount} cache(s) expiré(s) supprimé(s)`);
                    resolve(deletedCount);
                }
            };

            request.onerror = () => reject(request.error);
        });
    }

    /**
     * Purge les données pédagogiques en cache pour le professeur (téléphone partagé)
     * NE TOUCHE JAMAIS au store pendingSync !
     */
    async clearTeacherOfflineData(ecoleId = null, userId = null) {
        if (!this.db) await this.init();
        const key = this.getTeacherCacheKey(ecoleId, userId);

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readwrite');
            const store = tx.objectStore('cachedData');
            const request = store.delete(key);

            request.onsuccess = () => {
                console.log(`🧹 Cache pédagogique professeur purgé (${key}), pendingSync préservé.`);
                resolve(true);
            };

            request.onerror = () => {
                console.warn(`⚠️ Échec suppression cache ${key}:`, request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Purge les données d'administration en cache pour l'admin (ordinateur partagé)
     * NE TOUCHE JAMAIS au store pendingSync !
     */
    async clearAdminOfflineData(ecoleId = null, userId = null) {
        if (!this.db) await this.init();
        const key = this.getAdminCacheKey(ecoleId, userId);

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readwrite');
            const store = tx.objectStore('cachedData');
            const request = store.delete(key);

            request.onsuccess = () => {
                console.log(`🧹 Cache administrateur purgé (${key}), pendingSync préservé.`);
                resolve(true);
            };

            request.onerror = () => {
                console.warn(`⚠️ Échec suppression cache ${key}:`, request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Purge les données en cache pour le parent (téléphone partagé)
     * NE TOUCHE JAMAIS au store pendingSync !
     */
    async clearParentOfflineData(ecoleId = null, userId = null) {
        if (!this.db) await this.init();
        const key = this.getParentCacheKey(ecoleId, userId);

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['cachedData'], 'readwrite');
            const store = tx.objectStore('cachedData');
            const request = store.delete(key);

            request.onsuccess = () => {
                console.log(`🧹 Cache parent purgé (${key}), pendingSync préservé.`);
                resolve(true);
            };

            request.onerror = () => {
                console.warn(`⚠️ Échec suppression cache ${key}:`, request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Purge globale de toutes les données privées de l'utilisateur actif lors du logout
     */
    async clearAllUserPrivateData(ecoleId = null, userId = null) {
        await this.clearTeacherOfflineData(ecoleId, userId);
        await this.clearAdminOfflineData(ecoleId, userId);
        await this.clearParentOfflineData(ecoleId, userId);
        return true;
    }
}

// Instance singleton
const offlineDB = new OfflineDB();

// Initialiser automatiquement côté navigateur
if (typeof window !== 'undefined') {
    window.offlineDB = offlineDB;
    offlineDB.init().catch(err => {
        console.error('❌ Échec initialisation IndexedDB:', err);
    });
}

// Export pour module/tests
if (typeof module !== 'undefined' && module.exports) {
    module.exports = offlineDB;
}
