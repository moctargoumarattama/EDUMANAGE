// static/js/db.js - Gestionnaire IndexedDB
class OfflineDB {
    constructor() {
        this.dbName = 'EcoleDB';
        this.version = 1;
        this.db = null;
    }

    /**
     * Initialiser la base de données
     */
    async init() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.version);

            request.onerror = () => {
                console.error('❌ Erreur ouverture IndexedDB:', request.error);
                reject(request.error);
            };

            request.onsuccess = () => {
                this.db = request.result;
                console.log('✅ IndexedDB initialisée');
                resolve(this.db);
            };

            request.onupgradeneeded = (event) => {
                console.log('🔧 Création/Mise à jour de la structure IndexedDB');
                const db = event.target.result;

                // Store pour les données en attente de synchronisation
                if (!db.objectStoreNames.contains('pendingSync')) {
                    const syncStore = db.createObjectStore('pendingSync', { 
                        keyPath: 'id', 
                        autoIncrement: true 
                    });
                    syncStore.createIndex('type', 'type', { unique: false });
                    syncStore.createIndex('timestamp', 'timestamp', { unique: false });
                    syncStore.createIndex('synced', 'synced', { unique: false });
                }

                // Store pour le cache de données
                if (!db.objectStoreNames.contains('cachedData')) {
                    const cacheStore = db.createObjectStore('cachedData', { 
                        keyPath: 'key' 
                    });
                    cacheStore.createIndex('expiry', 'expiry', { unique: false });
                }

                // Store pour les logs
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

    /**
     * Ajouter une donnée en attente de synchronisation
     */
    async addPendingSync(data) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readwrite');
            const store = tx.objectStore('pendingSync');

            const dataToStore = {
                ...data,
                timestamp: Date.now(),
                synced: false,
                retries: 0
            };

            const request = store.add(dataToStore);

            request.onsuccess = () => {
                console.log('✅ Donnée ajoutée à la queue de sync:', request.result);
                resolve(request.result);
            };

            request.onerror = () => {
                console.error('❌ Erreur ajout donnée:', request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Récupérer toutes les données en attente
     */
    async getAllPending() {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readonly');
            const store = tx.objectStore('pendingSync');
            const request = store.getAll();

            request.onsuccess = () => {
                const data = request.result.filter(item => !item.synced);
                console.log(`📊 ${data.length} donnée(s) en attente de sync`);
                resolve(data);
            };

            request.onerror = () => {
                console.error('❌ Erreur lecture données:', request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Récupérer les données par type
     */
    async getPendingByType(type) {
        if (!this.db) await this.init();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(['pendingSync'], 'readonly');
            const store = tx.objectStore('pendingSync');
            const index = store.index('type');
            const request = index.getAll(type);

            request.onsuccess = () => {
                const data = request.result.filter(item => !item.synced);
                resolve(data);
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

        const allData = await this.getAllPending();
        const syncedIds = allData.filter(item => item.synced).map(item => item.id);

        for (const id of syncedIds) {
            await this.deletePending(id);
        }

        console.log(`🗑️ ${syncedIds.length} donnée(s) synchronisée(s) supprimée(s)`);
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
     * Obtenir des statistiques
     */
    async getStats() {
        if (!this.db) await this.init();

        const allData = await this.getAllPending();
        
        const stats = {
            total: allData.length,
            notes: allData.filter(d => d.type === 'note').length,
            absences: allData.filter(d => d.type === 'absence').length,
            paiements: allData.filter(d => d.type === 'paiement').length,
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

                // Vérifier l'expiration
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
}

// Instance singleton
const offlineDB = new OfflineDB();

// Initialiser automatiquement
if (typeof window !== 'undefined') {
    offlineDB.init().catch(err => {
        console.error('❌ Échec initialisation IndexedDB:', err);
    });
}

// Export pour utilisation
if (typeof module !== 'undefined' && module.exports) {
    module.exports = offlineDB;
}