// static/js/pwa.js - Module PWA Centralisé KLASORA
(function () {
    'use strict';

    let deferredPrompt = null;
    let swRegistration = null;
    const DISMISS_DELAY_DAYS = 7;
    const DISMISS_KEY = 'klasora_pwa_later_at';
    const INSTALLED_KEY = 'klasora_pwa_installed';

    // 1. Détection du mode d'affichage installé
    function isKlasoraInstalled() {
        return (
            window.matchMedia('(display-mode: standalone)').matches ||
            window.matchMedia('(display-mode: fullscreen)').matches ||
            window.navigator.standalone === true ||
            document.referrer.includes('android-app://') ||
            localStorage.getItem(INSTALLED_KEY) === 'true'
        );
    }

    // 2. Détection iOS Safari
    function isIos() {
        const ua = window.navigator.userAgent.toLowerCase();
        return /iphone|ipad|ipod/.test(ua);
    }

    function isIosSafari() {
        const ua = window.navigator.userAgent.toLowerCase();
        return isIos() && !ua.includes('crios') && !ua.includes('fxios') && !window.navigator.standalone;
    }

    // 3. Vérification si la proposition doit être différée (Plus tard)
    function isDismissedRecently() {
        const dismissedAt = localStorage.getItem(DISMISS_KEY);
        if (!dismissedAt) return false;
        const diffMs = Date.now() - parseInt(dismissedAt, 10);
        const diffDays = diffMs / (1000 * 60 * 60 * 24);
        return diffDays < DISMISS_DELAY_DAYS;
    }

    // 4. Affichage du popup d'installation
    function showInstallPrompt() {
        if (!navigator.onLine) {
            hideInstallPrompt();
            return;
        }
        if (isKlasoraInstalled()) return;

        const container = document.getElementById('klasoraPwaInstallContainer');
        if (!container) return;

        // Si sur iOS, afficher les instructions spécifiques
        const iosBox = document.getElementById('klasoraIosInstructions');
        const standardActions = document.getElementById('klasoraStandardInstallActions');

        if (isIosSafari()) {
            if (iosBox) iosBox.classList.remove('d-none');
            if (standardActions) standardActions.classList.add('d-none');
        } else {
            if (iosBox) iosBox.classList.add('d-none');
            if (standardActions) standardActions.classList.remove('d-none');
        }

        container.classList.remove('d-none');
        container.setAttribute('aria-hidden', 'false');
    }

    // 5. Fermeture du popup
    function hideInstallPrompt() {
        const container = document.getElementById('klasoraPwaInstallContainer');
        if (!container) return;
        container.classList.add('d-none');
        container.setAttribute('aria-hidden', 'true');
    }

    function syncNetworkUiState() {
        if (!navigator.onLine) {
            hideInstallPrompt();
            showNetworkToast(false);
        }
    }

    // 6. Gestion du clic "Installer KLASORA"
    async function handleInstallClick() {
        if (!deferredPrompt) {
            console.log('[PWA] Aucun prompt natif disponible immédiatement');
            showInstallPrompt();
            return;
        }

        try {
            deferredPrompt.prompt();
            const choiceResult = await deferredPrompt.userChoice;
            console.log('[PWA] Choix utilisateur:', choiceResult.outcome);

            if (choiceResult.outcome === 'accepted') {
                localStorage.setItem(INSTALLED_KEY, 'true');
                hideInstallPrompt();
                updateInstallButtonsVisibility(true);
            } else {
                localStorage.setItem(DISMISS_KEY, Date.now().toString());
                hideInstallPrompt();
            }
            deferredPrompt = null;
        } catch (err) {
            console.error("[PWA] Erreur lors du prompt d'installation:", err);
            hideInstallPrompt();
        }
    }

    // 7. Gestion du clic "Plus tard"
    function handleLaterClick() {
        localStorage.setItem(DISMISS_KEY, Date.now().toString());
        hideInstallPrompt();
    }

    // 8. Mise à jour de la visibilité des boutons d'installation dans l'UI (Menu, Sidebar)
    function updateInstallButtonsVisibility(isInstalled) {
        const buttons = document.querySelectorAll('.btn-install-klasora-trigger');
        buttons.forEach(btn => {
            if (isInstalled) {
                btn.classList.add('d-none');
            } else {
                btn.classList.remove('d-none');
            }
        });
    }

    // 9. Gestion de l'indicateur de réseau discret
    function showNetworkToast(isOnline) {
        const toast = document.getElementById('klasoraNetworkToast');
        const dot = document.getElementById('klasoraToastDot');
        const text = document.getElementById('klasoraToastText');
        if (!toast || !dot || !text) return;

        toast.classList.remove('d-none');
        if (isOnline) {
            dot.className = 'klasora-toast-dot online';
            text.textContent = 'Connexion rétablie';
            setTimeout(() => {
                toast.classList.add('d-none');
            }, 3000);
        } else {
            dot.className = 'klasora-toast-dot offline';
            text.textContent = 'Vous êtes hors connexion';
        }
    }

    // 10. Enregistrement du Service Worker
    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) return;

        try {
            swRegistration = await navigator.serviceWorker.register('/service-worker.js', {
                scope: '/'
            });

            console.log('[PWA] Service Worker actif, scope:', swRegistration.scope);

            // Détection des mises à jour du Service Worker
            swRegistration.addEventListener('updatefound', () => {
                const newWorker = swRegistration.installing;
                if (!newWorker) return;

                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        // Nouvelle version prête
                        const updateToast = document.getElementById('klasoraUpdateToast');
                        if (updateToast) updateToast.classList.remove('d-none');
                    }
                });
            });

            // Rechargement propre quand le nouveau SW prend le contrôle
            let refreshing = false;
            navigator.serviceWorker.addEventListener('controllerchange', () => {
                if (!refreshing) {
                    // Éviter le rechargement brutal si l'utilisateur est en train de taper dans un formulaire
                    const activeEl = document.activeElement;
                    const tag = activeEl ? activeEl.tagName.toLowerCase() : '';
                    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
                        console.log('[PWA] Saisie en cours, rechargement automatique différé');
                        return;
                    }
                    refreshing = true;
                    window.location.reload();
                }
            });

        } catch (error) {
            console.warn('[PWA] Erreur enregistrement Service Worker:', error);
        }
    }

    // 11. Initialisation au chargement du DOM
    document.addEventListener('DOMContentLoaded', () => {
        // Enregistrer le SW
        registerServiceWorker();

        // Récupérer le contexte utilisateur injecté depuis le backend
        const userContextEl = document.getElementById('klasoraUserContext');
        const userRole = userContextEl ? userContextEl.getAttribute('data-user-role') : null;
        const isAuthenticated = userContextEl ? userContextEl.getAttribute('data-authenticated') === 'true' : false;
        const currentPath = window.location.pathname;

        const alreadyInstalled = isKlasoraInstalled();
        updateInstallButtonsVisibility(alreadyInstalled);

        // Événement avant installation native Android
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            console.log('[PWA] Événement beforeinstallprompt capturé');

            // Affichage automatique pour le parent, le professeur ou l'admin connecté sur son dashboard
            const isParentDashboard = currentPath.startsWith('/parent');
            const isTeacherDashboard = currentPath.startsWith('/professeur');
            const isAdminDashboard = currentPath.startsWith('/admin') && !currentPath.includes('/support');
            const isAllowedRole = (isAuthenticated && (isParentDashboard || isTeacherDashboard || isAdminDashboard));
            const isExcludedPage = currentPath.includes('/login') || currentPath.includes('/onboarding') || currentPath.includes('/aide');

            if (!alreadyInstalled && !isDismissedRecently() && isAllowedRole && !isExcludedPage) {
                // Délai de courtoisie de 2 secondes pour ne pas surprendre l'utilisateur
                setTimeout(() => {
                    showInstallPrompt();
                }, 2000);
            }
        });

        // Événement après installation réussie
        window.addEventListener('appinstalled', () => {
            console.log('[PWA] Application KLASORA installée avec succès !');
            localStorage.setItem(INSTALLED_KEY, 'true');
            hideInstallPrompt();
            updateInstallButtonsVisibility(true);
            deferredPrompt = null;
        });

        // Liaison des boutons de la popup
        const installBtn = document.getElementById('klasoraPwaInstallBtn');
        if (installBtn) {
            installBtn.addEventListener('click', handleInstallClick);
        }

        const laterBtn = document.getElementById('klasoraPwaLaterBtn');
        if (laterBtn) {
            laterBtn.addEventListener('click', handleLaterClick);
        }

        const closeBtn = document.getElementById('klasoraPwaCloseBtn');
        if (closeBtn) {
            closeBtn.addEventListener('click', hideInstallPrompt);
        }

        const backdrop = document.getElementById('klasoraPwaBackdrop');
        if (backdrop) {
            backdrop.addEventListener('click', hideInstallPrompt);
        }

        // Liaison des boutons de déclenchement manuel dans le menu
        const manualTriggers = document.querySelectorAll('.btn-install-klasora-trigger');
        manualTriggers.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                if (deferredPrompt) {
                    handleInstallClick();
                } else {
                    showInstallPrompt();
                }
            });
        });

        // Bouton de mise à jour du Service Worker
        const updateBtn = document.getElementById('klasoraUpdateAppBtn');
        if (updateBtn) {
            updateBtn.addEventListener('click', () => {
                if (swRegistration && swRegistration.waiting) {
                    swRegistration.waiting.postMessage({ type: 'SKIP_WAITING' });
                } else {
                    window.location.reload();
                }
            });
        }

        // Écouteurs de réseau
        window.addEventListener('online', () => showNetworkToast(true));
        window.addEventListener('offline', syncNetworkUiState);
        window.addEventListener('pageshow', syncNetworkUiState);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) syncNetworkUiState();
        });
        syncNetworkUiState();
    });

    // 12. Fonction utilitaire globale pour nettoyage au logout
    window.klasoraPwaCleanOnLogout = function () {
        try {
            if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_USER_CACHE' });
            }
            localStorage.removeItem('klasora_pwa_cached_user');
            // Purger les données pédagogiques du professeur sur téléphone partagé (pendingSync préservé)
            if (window.offlineDB && typeof window.offlineDB.clearTeacherOfflineData === 'function') {
                window.offlineDB.clearTeacherOfflineData();
            }
            // Purger les données d'administration sur appareil partagé (pendingSync préservé)
            if (window.offlineDB && typeof window.offlineDB.clearAdminOfflineData === 'function') {
                window.offlineDB.clearAdminOfflineData();
            }
        } catch (e) {
            console.warn('[PWA] Erreur nettoyage logout:', e);
        }
    };

    // Exposer l'état d'installation
    window.isKlasoraInstalled = isKlasoraInstalled;
})();
