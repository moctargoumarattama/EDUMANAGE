// ==================================================
// EDUMANAGE - JAVASCRIPT FRONTEND COMMUN
// Interactions d'interface & comportements visuels
// ==================================================

document.addEventListener('DOMContentLoaded', function() {
    // 1. Initialisation des tooltips Bootstrap
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(function(tooltipTriggerEl) {
        new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // 2. Validation Bootstrap standard pour les formulaires .needs-validation
    const forms = document.querySelectorAll('.needs-validation');
    Array.prototype.slice.call(forms).forEach(function(form) {
        form.addEventListener('submit', function(event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        }, false);
    });

    document.querySelectorAll('[data-progress]').forEach(function(bar) {
        const value = Math.max(0, Math.min(100, parseInt(bar.dataset.progress || '0', 10)));
        bar.style.width = value + '%';
    });

    // 3. Calcul de moyenne reutilisable
    window.calculerMoyenne = function(notes, coefficients) {
        let total = 0;
        let totalCoeff = 0;
        notes.forEach(function(note, index) {
            if (note && coefficients && coefficients[index]) {
                total += parseFloat(note) * parseFloat(coefficients[index]);
                totalCoeff += parseFloat(coefficients[index]);
            }
        });
        return totalCoeff > 0 ? (total / totalCoeff).toFixed(2) : '0.00';
    };
});


// ==========================================================================
// MOTEUR DE PAGINATION FRONTEND UNIFIÉ (KLASORA CLEAN & RESPONSIVE)
// Utilisable sur toutes les listes dynamiques (Cours, Notes, etc.)
// ==========================================================================
window.renderKlasoraPagination = function(options) {
    if (!options) return;
    const {
        container,
        listEl,
        mobileEl,
        jumpEl,
        jumpContainer,
        badgeEl,
        textEl,
        totalItems = 0,
        totalPages = 1,
        currentPage = 1,
        startIndex = 0,
        endIndex = 0,
        itemLabel = 'élément',
        itemLabelPlural = itemLabel + 's',
        badgeIcon = 'fa-list',
        scrollToEl = null,
        onPageChange = null
    } = options;

    if (!container) return;

    if (totalItems === 0 || totalPages <= 1) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';
    const actualEnd = Math.min(endIndex, totalItems);

    // 1. Badge compteur
    if (badgeEl) {
        badgeEl.innerHTML = `<i class="fas ${badgeIcon} me-1"></i>${totalItems} ${totalItems > 1 ? itemLabelPlural : itemLabel}`;
    }

    // 2. Texte de pagination
    if (textEl) {
        textEl.innerHTML = `Affichage de <strong class="text-dark">${startIndex + 1}</strong> à <strong class="text-dark">${actualEnd}</strong> sur <strong class="text-dark">${totalItems}</strong> ${totalItems > 1 ? itemLabelPlural : itemLabel} <span class="text-secondary opacity-75 d-none d-lg-inline">&bull; Page ${currentPage} sur ${totalPages}</span>`;
    }

    // 3. Vue Mobile (< 576px)
    if (mobileEl) {
        mobileEl.innerHTML = `
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill px-3 py-1.5 d-inline-flex align-items-center gap-1 shadow-2xs ${currentPage <= 1 ? 'opacity-50' : ''}" 
                    data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''}>
                <i class="fas fa-chevron-left fa-xs"></i>
                <span>Précédent</span>
            </button>
            <span class="badge bg-light text-dark border px-3 py-2 rounded-pill fw-semibold shadow-2xs">
                Page ${currentPage} / ${totalPages}
            </span>
            <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill px-3 py-1.5 d-inline-flex align-items-center gap-1 shadow-2xs ${currentPage >= totalPages ? 'opacity-50' : ''}" 
                    data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''}>
                <span>Suivant</span>
                <i class="fas fa-chevron-right fa-xs"></i>
            </button>
        `;
    }

    // 4. Vue Tablette / Desktop (>= 576px)
    if (listEl) {
        let html = '';

        // Première page («)
        if (currentPage > 2) {
            html += `<li class="page-item" title="Première page">
                <button type="button" class="page-link rounded-start-pill" data-page="1" aria-label="Première page">
                    <i class="fas fa-angles-left fa-xs"></i>
                </button>
            </li>`;
        }

        // Précédent (‹)
        html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <button type="button" class="page-link ${currentPage <= 2 ? 'rounded-start-pill' : ''}" data-page="${currentPage - 1}" aria-label="Précédent" ${currentPage === 1 ? 'disabled' : ''} title="Page précédente">
                <i class="fas fa-chevron-left fa-xs"></i>
            </button>
        </li>`;

        // Calcul des pages visibles (1 autour de la page courante)
        const startPage = Math.max(1, currentPage - 1);
        const endPage = Math.min(totalPages, currentPage + 1);

        if (startPage > 1) {
            html += `<li class="page-item"><button type="button" class="page-link" data-page="1">1</button></li>`;
            if (startPage > 2) {
                html += `<li class="page-item disabled ellipsis"><span class="page-link">&hellip;</span></li>`;
            }
        }

        for (let p = startPage; p <= endPage; p++) {
            if (p === currentPage) {
                html += `<li class="page-item active" aria-current="page">
                    <span class="page-link">${p}</span>
                </li>`;
            } else {
                html += `<li class="page-item">
                    <button type="button" class="page-link" data-page="${p}">${p}</button>
                </li>`;
            }
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                html += `<li class="page-item disabled ellipsis"><span class="page-link">&hellip;</span></li>`;
            }
            html += `<li class="page-item"><button type="button" class="page-link" data-page="${totalPages}">${totalPages}</button></li>`;
        }

        // Suivant (›)
        html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
            <button type="button" class="page-link ${currentPage >= totalPages - 1 ? 'rounded-end-pill' : ''}" data-page="${currentPage + 1}" aria-label="Suivant" ${currentPage === totalPages ? 'disabled' : ''} title="Page suivante">
                <i class="fas fa-chevron-right fa-xs"></i>
            </button>
        </li>`;

        // Dernière page (»)
        if (currentPage < totalPages - 1) {
            html += `<li class="page-item" title="Dernière page">
                <button type="button" class="page-link rounded-end-pill" data-page="${totalPages}" aria-label="Dernière page">
                    <i class="fas fa-angles-right fa-xs"></i>
                </button>
            </li>`;
        }

        listEl.innerHTML = html;
    }

    // 5. Sélecteur d'accès rapide (Aller à la page)
    if (jumpEl && jumpContainer) {
        if (totalPages > 5) {
            let optionsHtml = '';
            for (let p = 1; p <= totalPages; p++) {
                optionsHtml += `<option value="${p}" ${p === currentPage ? 'selected' : ''}>p. ${p}</option>`;
            }
            jumpEl.innerHTML = optionsHtml;
            jumpContainer.classList.remove('d-none');
            jumpContainer.classList.add('d-flex');
        } else {
            jumpContainer.classList.remove('d-flex');
            jumpContainer.classList.add('d-none');
        }
    }

    // 6. Gestionnaire de clic commun
    const handlePageSelection = function(targetPage) {
        if (targetPage && targetPage !== currentPage && targetPage >= 1 && targetPage <= totalPages) {
            if (typeof onPageChange === 'function') {
                onPageChange(targetPage);
            }
            if (scrollToEl) {
                scrollToEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    };

    if (listEl) {
        listEl.querySelectorAll('button.page-link').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                const p = parseInt(this.dataset.page, 10);
                handlePageSelection(p);
            });
        });
    }

    if (mobileEl) {
        mobileEl.querySelectorAll('button[data-page]').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                const p = parseInt(this.dataset.page, 10);
                handlePageSelection(p);
            });
        });
    }

    if (jumpEl) {
        jumpEl.onchange = function() {
            const p = parseInt(this.value, 10);
            handlePageSelection(p);
        };
    }
};
