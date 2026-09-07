/**
 * KLASORA - Moteur de visite guidee interactive (Spotlight Tour)
 * Vanilla JavaScript sans dependance externe
 */

(function () {
    'use strict';

    const TOUR_STEPS = [
        {
            target: '[data-tour="eleves"]',
            title: 'Élèves',
            description: 'Gérez les élèves inscrits dans votre établissement, leurs dossiers, inscriptions et parcours scolaires.'
        },
        {
            target: '[data-tour="professeurs"]',
            title: 'Professeurs',
            description: 'Ajoutez les enseignants, gérez leurs profils et suivez leurs affectations.'
        },
        {
            target: '[data-tour="classes"]',
            title: 'Classes',
            description: 'Organisez vos élèves et vos professeurs par classe pour l\'année scolaire active.'
        },
        {
            target: '[data-tour="cours"]',
            title: 'Cours & Matières',
            description: 'Consultez et configurez les matières enseignées et les programmes de formation.'
        },
        {
            target: '[data-tour="annees"]',
            title: 'Années scolaires',
            description: 'Gérez vos années scolaires, planifiez les rentrées et basculez l\'année active en un clic.'
        },
        {
            target: '[data-tour="emplois"]',
            title: 'Emploi du temps',
            description: 'Planifiez les créneaux horaires des cours, l\'attribution des salles et la charge horaire.'
        },
        {
            target: '[data-tour="paiements"]',
            title: 'Paiements & Scolarité',
            description: 'Suivez les règlements, les relances et la situation financière de chaque élève.'
        },
        {
            target: '[data-tour="rapports"]',
            title: 'Rapports & Statistiques',
            description: 'Consultez les statistiques clés, effectifs et performances de votre établissement.'
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

    function findValidSteps() {
        return TOUR_STEPS.filter(step => {
            const el = document.querySelector(step.target);
            return el && el.offsetParent !== null; // élément visible
        });
    }

    function createOverlayElements() {
        if (!spotlightEl) {
            spotlightEl = document.createElement('div');
            spotlightEl.className = 'tour-spotlight-box';
            document.body.appendChild(spotlightEl);
        }

        if (!popoverEl) {
            popoverEl = document.createElement('div');
            popoverEl.className = 'tour-popover';
            popoverEl.setAttribute('role', 'dialog');
            popoverEl.setAttribute('aria-modal', 'true');
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
    }

    function updatePosition() {
        if (!isTourActive || !validSteps[currentStepIndex]) return;

        const step = validSteps[currentStepIndex];
        const targetEl = document.querySelector(step.target);
        if (!targetEl || !spotlightEl || !popoverEl) return;

        const rect = targetEl.getBoundingClientRect();
        const padding = 6;

        // Positionnement du spotlight
        spotlightEl.style.top = (rect.top - padding) + 'px';
        spotlightEl.style.left = (rect.left - padding) + 'px';
        spotlightEl.style.width = (rect.width + padding * 2) + 'px';
        spotlightEl.style.height = (rect.height + padding * 2) + 'px';

        // Positionnement de la bulle (popover)
        const popoverWidth = Math.min(350, window.innerWidth - 32);
        const popoverHeight = popoverEl.offsetHeight || 180;
        const viewportHeight = window.innerHeight;
        const viewportWidth = window.innerWidth;

        let top = 0;
        let left = 0;

        if (viewportWidth < 576) {
            left = 16;
            if (rect.bottom + popoverHeight + 20 < viewportHeight) {
                top = rect.bottom + 12;
            } else if (rect.top - popoverHeight - 20 > 0) {
                top = rect.top - popoverHeight - 12;
            } else {
                top = Math.max(16, viewportHeight - popoverHeight - 16);
            }
        } else {
            left = Math.max(16, Math.min(viewportWidth - popoverWidth - 16, rect.left + (rect.width / 2) - (popoverWidth / 2)));
            if (rect.bottom + popoverHeight + 20 < viewportHeight) {
                top = rect.bottom + 14;
            } else {
                top = Math.max(16, rect.top - popoverHeight - 14);
            }
        }

        popoverEl.style.top = top + 'px';
        popoverEl.style.left = left + 'px';
        popoverEl.style.width = popoverWidth + 'px';
    }

    function renderStep(index) {
        if (index < 0 || index >= validSteps.length) {
            finishTour();
            return;
        }

        currentStepIndex = index;
        const step = validSteps[currentStepIndex];
        const targetEl = document.querySelector(step.target);

        if (!targetEl) {
            renderStep(index + 1);
            return;
        }

        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });

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

        setTimeout(updatePosition, 120);
        setTimeout(updatePosition, 320);
    }

    function startTour(isManual) {
        validSteps = findValidSteps();
        if (validSteps.length === 0) {
            console.warn('[KlasoraTour] Aucun élément cible disponible pour le tour.');
            return;
        }

        createOverlayElements();
        isTourActive = true;

        window.addEventListener('resize', updatePosition);
        window.addEventListener('scroll', updatePosition, true);

        renderStep(0);
    }

    function skipTour() {
        if (confirm('Souhaitez-vous passer la visite guidée ? Vous pourrez la relancer à tout moment depuis le menu utilisateur.')) {
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
            if (!sessionStorage.getItem('klasora_tour_dismissed_session')) {
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
