/**
 * KLASORA - Tiroir Latéral Coulissant (Offcanvas Drawer) : Point du jour
 * Vanilla JavaScript sans dépendance externe
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const drawerEl = document.getElementById('offcanvasPointDuJour');
        if (!drawerEl) return;

        const contentArea = document.getElementById('pointDuJourContentArea');
        const refreshBtn = document.getElementById('btnRefreshPointDuJour');
        const refreshIcon = document.getElementById('iconRefreshPointDuJour');
        let hasLoaded = false;
        let isLoading = false;

        function loadPointDuJourData(forceReload) {
            if (isLoading) return;
            if (hasLoaded && !forceReload) return;

            isLoading = true;
            if (refreshIcon) refreshIcon.classList.add('fa-spin');
            if (contentArea && (!hasLoaded || forceReload)) {
                contentArea.innerHTML = 
                    '<div class="d-flex flex-column align-items-center justify-content-center py-5 text-muted">' +
                        '<div class="spinner-border text-primary mb-3" role="status" style="width: 2.2rem; height: 2.2rem;">' +
                            '<span class="visually-hidden">Chargement...</span>' +
                        '</div>' +
                        '<p class="small fw-semibold mb-0">Chargement de votre point du jour...</p>' +
                    '</div>';
            }

            fetch('/point-du-jour?fragment=1', {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            })
            .then(function (res) {
                if (!res.ok) throw new Error('Erreur HTTP ' + res.status);
                return res.text();
            })
            .then(function (html) {
                if (contentArea) {
                    contentArea.innerHTML = html;
                    hasLoaded = true;
                }
            })
            .catch(function (err) {
                console.error('[PointDuJour Drawer] Erreur de chargement:', err);
                if (contentArea) {
                    contentArea.innerHTML = 
                        '<div class="alert alert-danger rounded-3 p-3 text-center my-3">' +
                            '<i class="fas fa-exclamation-triangle fa-2x mb-2 text-danger"></i>' +
                            '<p class="small mb-2 fw-semibold">Impossible de charger le point du jour pour le moment.</p>' +
                            '<button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-3" id="retryPointDuJourBtn">' +
                                '<i class="fas fa-redo me-1"></i>Réessayer' +
                            '</button>' +
                        '</div>';
                    const retryBtn = document.getElementById('retryPointDuJourBtn');
                    if (retryBtn) {
                        retryBtn.onclick = function () {
                            loadPointDuJourData(true);
                        };
                    }
                }
            })
            .finally(function () {
                isLoading = false;
                if (refreshIcon) refreshIcon.classList.remove('fa-spin');
            });
        }

        // Chargement automatique lors de l'ouverture du tiroir
        drawerEl.addEventListener('show.bs.offcanvas', function () {
            loadPointDuJourData(false);
        });

        // Bouton Rafraîchir
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function () {
                loadPointDuJourData(true);
            });
        }

        // Préchargement discret au survol des boutons déclencheurs
        const triggers = document.querySelectorAll('[data-bs-target="#offcanvasPointDuJour"]');
        triggers.forEach(function (trigger) {
            trigger.addEventListener('mouseenter', function () {
                if (!hasLoaded && !isLoading) {
                    loadPointDuJourData(false);
                }
            }, { once: true });
        });

        // Interception sur PC des liens vers /point-du-jour pour ouvrir le tiroir au lieu de recharger la page
        document.addEventListener('click', function (e) {
            if (window.innerWidth < 992) return;
            if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;

            const link = e.target.closest('a[href*="/point-du-jour"]');
            if (!link) return;

            // Ne pas intercepter le bouton "Plein écran" situé à l'intérieur du tiroir
            if (link.closest('#offcanvasPointDuJour')) return;

            e.preventDefault();
            if (typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
                const offcanvasInstance = bootstrap.Offcanvas.getOrCreateInstance(drawerEl);
                offcanvasInstance.show();
            }
        });
    });
})();
