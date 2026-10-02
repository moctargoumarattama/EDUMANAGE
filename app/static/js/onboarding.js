/**
 * KLASORA - Moteur de visite guidee interactive (Spotlight Tour)
 * Vanilla JavaScript sans dependance externe
 */

(function () {
    'use strict';

    const TOUR_STEPS = [
        // 1. Navigation latérale gauche
        {
            target: '[data-tour="sidebar-nav"]',
            title: 'Menu de Navigation Latérale',
            description: 'Accédez en 1 clic à tous les modules métier : Élèves, Professeurs, Classes, Notes, Bulletins, Emplois du temps et Comptabilité.'
        },

        // 2. Outils essentiels de la barre supérieure (Topbar)
        {
            target: '[data-tour="topbar-annee"]',
            title: 'Année Scolaire Active',
            description: 'Affiche l\'année en cours. Cliquez sur ce badge pour planifier la prochaine rentrée, gérer les passages ou archiver.'
        },
        {
            target: '[data-tour="topbar-recherche"]',
            title: 'Recherche Globale',
            description: 'Retrouvez en quelques secondes n\'importe quel élève, professeur ou cours dans toute la base de données.'
        },
        {
            target: '[data-tour="topbar-pointdujour"]',
            title: 'Point du jour (Tiroir rapide)',
            description: 'Ouvre instantanément le volet latéral avec les présences du matin, les alertes à traiter et le fil d\'activité en direct.'
        },
        {
            target: '[data-tour="topbar-alertes"]',
            title: 'Centre d\'Alertes Scolaires',
            description: 'Accédez en 1 clic au pop-up de vigilance : élèves n\'ayant jamais payé, retards de scolarité, absentéisme répété et difficultés scolaires avec traitement immédiat.'
        },
        {
            target: '[data-tour="topbar-visite"]',
            title: 'Bouton Visite Guidée',
            description: 'Permet de relancer ce guide interactif à n\'importe quel moment pour revoir l\'utilité de chaque bouton de la plateforme.'
        },

        // 3. Indicateurs clés d'accueil (KPI)
        {
            target: '[data-tour="eleves"]',
            title: 'Élèves Inscrits',
            description: 'Effectif total de l\'école. Cliquez pour ouvrir le répertoire des élèves, les dossiers individuels et générer les QR codes.'
        },
        {
            target: '[data-tour="professeurs"]',
            title: 'Corps Enseignant Actif',
            description: 'Nombre de professeurs en exercice. Cliquez pour consulter l\'équipe, leurs spécialités et leurs charges de cours.'
        },
        {
            target: '[data-tour="cours"]',
            title: 'Cours & Matières',
            description: 'Volume des matières enseignées. Cliquez pour configurer les programmes de cours par classe et leurs coefficients.'
        },
        {
            target: '[data-tour="paiements"]',
            title: 'Recouvrement & Impayés',
            description: 'Suivi des familles avec solde en attente. Cliquez pour voir le détail des échéances financières et relancer les règlements.'
        },

        // 4. Barre d'Actions Rapides
        {
            target: '[data-tour="action-inscrire"]',
            title: 'Bouton « + Inscrire un élève »',
            description: 'Ouvre instantanément le formulaire d\'inscription en 1 clic. Le compte d\'accès des parents est généré au même moment !'
        },
        {
            target: '[data-tour="action-encaisser"]',
            title: 'Bouton « + Encaisser scolarité »',
            description: 'Enregistrez un versement en quelques secondes et imprimez immédiatement le reçu officiel avec son numéro unique.'
        },
        {
            target: '[data-tour="action-affecter"]',
            title: 'Bouton « + Affecter un cours »',
            description: 'Rattachez rapidement une matière, un coefficient et un enseignant à une classe sans quitter votre tableau de bord.'
        },
        {
            target: '[data-tour="action-annee"]',
            title: 'Bouton « Année scolaire »',
            description: 'Accédez à la gestion des sessions et lancez l\'assistant de passage de classe pour préparer la nouvelle rentrée.'
        },

        // 5. Suivi quotidien (Widgets opérationnels)
        {
            target: '[data-tour="widget-presences"]',
            title: 'Suivi des Présences du Jour',
            description: 'Taux d\'assiduité en direct de l\'établissement. Le bouton « Gérer les absences » permet de faire l\'appel et de justifier les retards.'
        },
        {
            target: '[data-tour="widget-versements"]',
            title: 'Derniers Versements Enregistrés',
            description: 'Historique des 5 derniers encaissements en caisse. Le bouton « Voir tout » donne accès à la comptabilité complète.'
        },

        // 6. Assistant IA interactif
        {
            target: '[data-tour="assistant-widget"]',
            title: 'Assistant IA de Direction',
            description: 'Votre copilote intelligent disponible à tout moment ! Posez-lui vos questions sur vos élèves ou demandez-lui de vous guider.'
        },

        // 7. Fallbacks pour mobile (Cartes d'administration mobile)
        {
            target: '[data-tour="classes"]',
            title: 'Classes & Niveaux',
            description: 'Organisez vos élèves et vos professeurs par classe pour l\'année scolaire active.'
        },
        {
            target: '[data-tour="emplois"]',
            title: 'Emploi du temps',
            description: 'Planifiez les créneaux horaires des cours, l\'attribution des salles et la charge horaire.'
        },
        {
            target: '[data-tour="widget-presences"]',
            title: 'Assiduité & Présences',
            description: 'Suivez le taux d\'assiduité en temps réel et accédez au suivi contextuel des absences.'
        }
    ];

    let currentStepIndex = 0;
    let validSteps = [];
    let spotlightEl = null;
    let popoverEl = null;
    let isTourActive = false;

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function saveTourCompleted() {
        const token = getCsrfToken();
        fetch('/api/admin/tour/complete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': token
            }
        })
        .then(res => res.json())
        .then(data => {
            if (data && data.success) {
                console.log('[KlasoraTour] Visite guidée enregistrée avec succès.');
            }
        })
        .catch(err => console.error('[KlasoraTour] Erreur enregistrement tour:', err));
    }

    function isElementVisible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function findValidSteps() {
        return TOUR_STEPS.filter(step => {
            const el = document.querySelector(step.target);
            return isElementVisible(el);
        });
    }

    function closeMobileMenuBeforeTour(callback) {
        const navbarCollapse = document.getElementById('navbarNav');
        const isOpen = navbarCollapse && navbarCollapse.classList.contains('show');

        if (!isOpen || typeof bootstrap === 'undefined' || !bootstrap.Collapse) {
            callback();
            return;
        }

        const startAfterClose = function () {
            navbarCollapse.removeEventListener('hidden.bs.collapse', startAfterClose);
            document.body.classList.remove('mobile-menu-open');
            setTimeout(callback, 80);
        };

        navbarCollapse.addEventListener('hidden.bs.collapse', startAfterClose);
        bootstrap.Collapse.getOrCreateInstance(navbarCollapse, { toggle: false }).hide();
    }

    function handleKeyDown(e) {
        if (!isTourActive) return;
        if (e.key === 'Escape') {
            skipTour();
        } else if (e.key === 'ArrowRight') {
            if (currentStepIndex < validSteps.length - 1) {
                renderStep(currentStepIndex + 1);
            }
        } else if (e.key === 'ArrowLeft') {
            if (currentStepIndex > 0) {
                renderStep(currentStepIndex - 1);
            }
        }
    }

    function createOverlayElements() {
        if (!spotlightEl) {
            spotlightEl = document.createElement('div');
            spotlightEl.className = 'tour-spotlight-box';
            spotlightEl.style.opacity = '0';
            document.body.appendChild(spotlightEl);
        }

        if (!popoverEl) {
            popoverEl = document.createElement('div');
            popoverEl.className = 'tour-popover';
            popoverEl.setAttribute('role', 'dialog');
            popoverEl.setAttribute('aria-modal', 'true');
            popoverEl.style.opacity = '0';
            document.body.appendChild(popoverEl);
        }
    }

    function cleanupOverlayElements() {
        if (spotlightEl && spotlightEl.parentNode) {
            spotlightEl.parentNode.removeChild(spotlightEl);
            spotlightEl = null;
        }
        if (popoverEl && popoverEl.parentNode) {
            popoverEl.parentNode.removeChild(popoverEl);
            popoverEl = null;
        }
        isTourActive = false;
        window.removeEventListener('resize', updatePosition);
        window.removeEventListener('scroll', updatePosition, true);
        window.removeEventListener('keydown', handleKeyDown);
    }

    function updatePosition() {
        if (!isTourActive || !validSteps[currentStepIndex]) return;

        const step = validSteps[currentStepIndex];
        const targetEl = document.querySelector(step.target);
        if (!targetEl || !spotlightEl || !popoverEl) return;

        const rect = targetEl.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;

        const padding = 6;

        // Adaptation du border-radius au composant ciblé
        const compStyle = window.getComputedStyle(targetEl);
        if (compStyle && compStyle.borderRadius && compStyle.borderRadius !== '0px') {
            spotlightEl.style.borderRadius = compStyle.borderRadius;
        } else {
            spotlightEl.style.borderRadius = '14px';
        }

        // Positionnement du spotlight au millimètre autour du bouton
        spotlightEl.style.top = Math.max(0, rect.top - padding) + 'px';
        spotlightEl.style.left = Math.max(0, rect.left - padding) + 'px';
        spotlightEl.style.width = (rect.width + padding * 2) + 'px';
        spotlightEl.style.height = (rect.height + padding * 2) + 'px';
        spotlightEl.style.opacity = '1';

        // Positionnement de la bulle d'explication (popover)
        const popoverWidth = Math.min(360, window.innerWidth - 32);
        popoverEl.style.width = popoverWidth + 'px';
        const popoverHeight = popoverEl.offsetHeight || 190;
        const viewportHeight = window.innerHeight;
        const viewportWidth = window.innerWidth;

        let top = 0;
        let left = 0;

        if (viewportWidth >= 992 && rect.left < 80 && rect.width < 320) {
            // Cible sur la barre latérale gauche : positionner la bulle à droite de la sidebar
            left = Math.min(viewportWidth - popoverWidth - 20, rect.right + 20);
            top = Math.max(20, Math.min(viewportHeight - popoverHeight - 20, rect.top + 40));
        } else if (viewportWidth < 576) {
            // Mobile : centré horizontalement
            left = Math.max(16, Math.round((viewportWidth - popoverWidth) / 2));
            if (rect.bottom + popoverHeight + 20 <= viewportHeight) {
                top = rect.bottom + 12;
            } else if (rect.top - popoverHeight - 12 >= 10) {
                top = rect.top - popoverHeight - 12;
            } else {
                top = Math.max(16, viewportHeight - popoverHeight - 16);
            }
        } else {
            // Desktop standard : centré au niveau de la cible
            left = Math.max(20, Math.min(viewportWidth - popoverWidth - 20, rect.left + (rect.width / 2) - (popoverWidth / 2)));
            if (rect.bottom + popoverHeight + 20 <= viewportHeight) {
                top = rect.bottom + 14;
            } else if (rect.top - popoverHeight - 14 >= 10) {
                top = rect.top - popoverHeight - 14;
            } else {
                top = Math.max(20, Math.min(viewportHeight - popoverHeight - 20, rect.top + 20));
            }
        }

        popoverEl.style.top = Math.round(top) + 'px';
        popoverEl.style.left = Math.round(left) + 'px';
        popoverEl.style.opacity = '1';
    }

    function renderStep(index) {
        if (index < 0 || index >= validSteps.length) {
            finishTour();
            return;
        }

        currentStepIndex = index;
        const step = validSteps[currentStepIndex];
        const targetEl = document.querySelector(step.target);

        if (!targetEl || !isElementVisible(targetEl)) {
            renderStep(index + 1);
            return;
        }

        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });

        const isFirst = (currentStepIndex === 0);
        const isLast = (currentStepIndex === validSteps.length - 1);
        const stepDisplay = (currentStepIndex + 1) + ' / ' + validSteps.length;

        popoverEl.innerHTML = 
            '<div class="tour-popover-header">' +
                '<span class="tour-step-badge">' + stepDisplay + '</span>' +
                '<button type="button" class="btn-close btn-close-sm" aria-label="Fermer" id="tour-close-btn"></button>' +
            '</div>' +
            '<div class="tour-title">' + step.title + '</div>' +
            '<div class="tour-description">' + step.description + '</div>' +
            '<div class="tour-popover-footer">' +
                '<button type="button" class="tour-btn-skip" id="tour-skip-btn">Passer</button>' +
                '<div class="tour-nav-buttons">' +
                    (!isFirst ? '<button type="button" class="tour-btn-prev" id="tour-prev-btn"><i class="fas fa-chevron-left me-1"></i>Précédent</button>' : '') +
                    (!isLast ?
                        '<button type="button" class="tour-btn-next" id="tour-next-btn">Suivant<i class="fas fa-chevron-right ms-1"></i></button>' : 
                        '<button type="button" class="tour-btn-next tour-btn-finish" id="tour-finish-btn"><i class="fas fa-check me-1"></i>Terminer</button>') +
                '</div>' +
            '</div>';

        const closeBtn = document.getElementById('tour-close-btn');
        if (closeBtn) closeBtn.onclick = function () { skipTour(); };

        const skipBtn = document.getElementById('tour-skip-btn');
        if (skipBtn) skipBtn.onclick = function () { skipTour(); };

        const prevBtn = document.getElementById('tour-prev-btn');
        if (prevBtn) prevBtn.onclick = function () { renderStep(currentStepIndex - 1); };

        const nextBtn = document.getElementById('tour-next-btn');
        if (nextBtn) nextBtn.onclick = function () { renderStep(currentStepIndex + 1); };

        const finishBtn = document.getElementById('tour-finish-btn');
        if (finishBtn) finishBtn.onclick = function () { finishTour(); };

        updatePosition();
        setTimeout(updatePosition, 80);
        setTimeout(updatePosition, 200);
        setTimeout(updatePosition, 380);
        setTimeout(updatePosition, 600);
    }

    function startTour(isManual) {
        const navbarCollapse = document.getElementById('navbarNav');
        if (navbarCollapse && navbarCollapse.classList.contains('show')) {
            closeMobileMenuBeforeTour(function () {
                startTour(isManual);
            });
            return;
        }

        validSteps = findValidSteps();
        if (validSteps.length === 0) {
            console.warn('[KlasoraTour] Aucun élément cible disponible pour le tour.');
            return;
        }

        createOverlayElements();
        isTourActive = true;

        window.addEventListener('resize', updatePosition);
        window.addEventListener('scroll', updatePosition, true);
        window.addEventListener('keydown', handleKeyDown);

        renderStep(0);
    }

    function skipTour() {
        if (confirm('Souhaitez-vous quitter la visite guidée ? Vous pourrez la relancer à tout moment depuis le bouton « Visite guidée ».')) {
            saveTourCompleted();
            cleanupOverlayElements();
        }
    }

    function finishTour() {
        saveTourCompleted();
        cleanupOverlayElements();
    }

    window.KlasoraTour = {
        start: startTour,
        skip: skipTour,
        finish: finishTour
    };

    document.addEventListener('DOMContentLoaded', function () {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('start_tour') === '1') {
            setTimeout(function () {
                startTour(true);
            }, 300);
            return;
        }

        const promptModalEl = document.getElementById('tourPromptModal');
        if (promptModalEl && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
            const forceShow = promptModalEl.getAttribute('data-force-show') === '1';
            if (forceShow || !sessionStorage.getItem('klasora_tour_dismissed_session')) {
                setTimeout(function () {
                    const modal = new bootstrap.Modal(promptModalEl);
                    modal.show();

                    promptModalEl.addEventListener('hidden.bs.modal', function () {
                        sessionStorage.setItem('klasora_tour_dismissed_session', '1');
                    });
                }, 600);
            }
        }
    });
})();
