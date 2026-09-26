// static/js/offline-forms.js - Gestionnaire de formulaires hors-ligne & UI KLASORA V2

(function () {
    'use strict';

    function fieldValue(form, selector, fallback = '') {
        const field = form.querySelector(selector);
        return field ? field.value : fallback;
    }

    function selectedText(select, fallback = '') {
        return select && select.selectedOptions && select.selectedOptions[0]
            ? select.selectedOptions[0].text
            : fallback;
    }

    function canQueueOffline(form) {
        return !navigator.onLine &&
            typeof offlineManager !== 'undefined' &&
            offlineManager &&
            typeof offlineManager.addToSync === 'function' &&
            form &&
            !form.matches('form[data-klasora-form]') &&
            form.dataset.offlineQueueing !== '1';
    }

    function offlineRequiredMessage(path) {
        if (path.includes('/cours')) return 'Connexion Internet requise pour crÃ©er ou modifier un cours.';
        if (path.includes('/paiement') || path.includes('/paiements')) return 'Connexion Internet requise pour enregistrer un paiement.';
        if (path.includes('/eleve') || path.includes('/eleves') || path.includes('/ajouter_eleve') || path.includes('/modifier_eleve')) {
            return 'Connexion Internet requise pour crÃ©er ou modifier un Ã©lÃ¨ve.';
        }
        return 'Connexion Internet requise pour cette opÃ©ration.';
    }

    // 1. Mise à jour de l'indicateur d'état dans la navbar
    async function updateSyncBadge() {
        const badge = document.getElementById('klasoraSyncStatusBadge');
        const dot = document.getElementById('klasoraSyncIndicatorDot');
        const text = document.getElementById('klasoraSyncStatusText');

        if (!badge || !dot || !text) return;

        if (typeof offlineDB === 'undefined') return;

        try {
            const stats = await offlineDB.getStats();
            const isOnline = navigator.onLine;

            if (offlineManager && offlineManager.syncInProgress) {
                badge.className = 'badge rounded-pill bg-primary-subtle text-primary border border-primary-subtle px-3 py-2 d-flex align-items-center';
                dot.style.backgroundColor = '#0d6efd';
                text.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i> Synchronisation...';
                return;
            }

            if (!isOnline) {
                badge.className = 'badge rounded-pill bg-danger-subtle text-danger border border-danger-subtle px-3 py-2 d-flex align-items-center';
                dot.style.backgroundColor = '#dc3545';
                if (stats.conflicts > 0) {
                    text.textContent = `Hors ligne • ${stats.conflicts} conflit(s) • ${stats.pending} en attente`;
                } else {
                    text.textContent = stats.total > 0 ? `Hors ligne • ${stats.total} en attente` : 'Hors ligne';
                }
            } else if (stats.conflicts > 0) {
                badge.className = 'badge rounded-pill bg-danger-subtle text-danger border border-danger-subtle px-3 py-2 d-flex align-items-center';
                dot.style.backgroundColor = '#dc3545';
                text.innerHTML = `<i class="fas fa-exclamation-triangle me-1"></i> ${stats.conflicts} conflit(s) à résoudre`;
            } else if (stats.total > 0) {
                badge.className = 'badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle px-3 py-2 d-flex align-items-center';
                dot.style.backgroundColor = '#ffc107';
                text.textContent = `${stats.total} en attente de sync`;
            } else {
                badge.className = 'badge rounded-pill bg-success-subtle text-success border border-success-subtle px-3 py-2 d-flex align-items-center';
                dot.style.backgroundColor = '#28a745';
                text.textContent = 'Synchronisé';
            }
        } catch (e) {
            console.warn('Erreur mise à jour badge:', e);
        }
    }

    // 2. Affichage d'une alerte flottante conviviale
    function showNotification(message, type = 'success') {
        let container = document.getElementById('klasoraOfflineAlertContainer');
        if (!container) {
            container = document.createElement('div');
            container.id = 'klasoraOfflineAlertContainer';
            container.style.cssText = 'position: fixed; top: 80px; right: 20px; z-index: 9999; max-width: 400px; width: 90%;';
            document.body.appendChild(container);
        }

        const alertEl = document.createElement('div');
        alertEl.className = `alert alert-${type} alert-dismissible fade show shadow-lg rounded-4 mb-2 border-0`;
        alertEl.role = 'alert';

        const row = document.createElement('div');
        row.className = 'd-flex align-items-center';

        const icon = document.createElement('i');
        icon.className = `fas ${type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'} fa-lg me-2`;
        icon.setAttribute('aria-hidden', 'true');
        row.appendChild(icon);

        const text = document.createElement('div');
        const lines = Array.isArray(message) ? message : [message];
        lines.forEach((line, index) => {
            if (index > 0) text.appendChild(document.createElement('br'));
            const node = document.createElement(index > 0 ? 'small' : 'span');
            if (index > 0) node.className = 'text-muted';
            node.textContent = String(line || '');
            text.appendChild(node);
        });
        row.appendChild(text);
        alertEl.appendChild(row);

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.setAttribute('data-bs-dismiss', 'alert');
        close.setAttribute('aria-label', 'Fermer');
        alertEl.appendChild(close);

        container.appendChild(alertEl);
        setTimeout(() => {
            alertEl.classList.remove('show');
            setTimeout(() => alertEl.remove(), 300);
        }, 6000);
    }

    // 3. Interception du formulaire des Notes
    function setupNotesOffline() {
        const path = window.location.pathname;
        if (!path.includes('/notes')) return;

        const form = document.querySelector('form[action*="notes"]');
        if (!form) return;

        const valeurInput = form.querySelector('input[name="valeur"]');
        const eleveSelect = form.querySelector('select[name="eleve_id"]');
        const coursSelect = form.querySelector('select[name="cours_id"]');

        if (!valeurInput || !eleveSelect) return;

        form.addEventListener('submit', async function (e) {
            if (canQueueOffline(form)) {
                e.preventDefault();

                const eleve_id = eleveSelect.value;
                const cours_id = coursSelect ? coursSelect.value : null;
                const valeur = valeurInput.value;
                const type_eval = fieldValue(form, 'select[name="type_evaluation"]', 'Devoir') || 'Devoir';
                const coef = fieldValue(form, 'input[name="coefficient"]', 1.0) || 1.0;
                const periode = fieldValue(form, 'select[name="periode"]') || selectedText(form.querySelector('select[name="annee_id"]'));
                const date_eval = fieldValue(form, 'input[name="date_evaluation"]') || new Date().toISOString();
                const selectedEleveOption = eleveSelect.selectedOptions && eleveSelect.selectedOptions[0] ? eleveSelect.selectedOptions[0] : null;
                const selectedCoursOption = coursSelect && coursSelect.selectedOptions && coursSelect.selectedOptions[0] ? coursSelect.selectedOptions[0] : null;
                const classe_id = selectedCoursOption ? selectedCoursOption.getAttribute('data-classe-id') : (selectedEleveOption ? selectedEleveOption.getAttribute('data-classe-id') : null);
                const annee_id = fieldValue(form, 'input[name="annee_id"], select[name="annee_id"]') || null;

                if (!eleve_id || !cours_id || valeur === '') {
                    alert('Veuillez remplir les champs obligatoires (Élève, Cours, Note).');
                    return;
                }

                form.dataset.offlineQueueing = '1';
                try {
                    const studentName = selectedText(eleveSelect, 'Élève');
                    const baseVersionVal = fieldValue(form, 'input[name="base_version"], input[name="sync_version"]');
                    const noteIdVal = fieldValue(form, 'input[name="note_id"], input[name="id"]');
                    const notePayload = {
                        eleve_id: parseInt(eleve_id, 10),
                        cours_id: parseInt(cours_id, 10),
                        valeur: parseFloat(valeur),
                        type_evaluation: type_eval,
                        coefficient: parseFloat(coef),
                        periode: periode,
                        date_evaluation: date_eval,
                        classe_id: classe_id ? parseInt(classe_id, 10) : null,
                        annee_id: annee_id ? parseInt(annee_id, 10) : null
                    };
                    if (baseVersionVal) notePayload.base_version = parseInt(baseVersionVal, 10);
                    if (noteIdVal) notePayload.note_id = parseInt(noteIdVal, 10);
                    await offlineManager.addToSync('note', notePayload);

                    showNotification([
                        `Note de ${valeur}/20 pour ${studentName} enregistrée localement sur cet appareil !`,
                        "En attente de synchronisation dès le retour d'Internet."
                    ], 'warning');

                    // Réinitialiser le champ note pour la saisie suivante
                    valeurInput.value = '';
                    valeurInput.focus();

                    updateSyncBadge();

                } catch (err) {
                    console.error('Erreur enregistrement note hors-ligne:', err);
                    alert('Erreur de sauvegarde locale: ' + err.message);
                } finally {
                    delete form.dataset.offlineQueueing;
                }
            }
        });
    }
    // 4. Interception du formulaire des Absences
    function setupAbsencesOffline() {
        const path = window.location.pathname;
        if (!path.includes('/absences')) return;

        const form = document.querySelector('form[action*="absences"]');
        if (!form) return;

        const eleveSelect = form.querySelector('select[name="eleve_id"]');
        const dateInput = form.querySelector('input[name="date_absence"]');

        if (!eleveSelect) return;

        form.addEventListener('submit', async function (e) {
            if (canQueueOffline(form)) {
                e.preventDefault();

                const eleve_id = eleveSelect.value;
                const cours_id = fieldValue(form, 'select[name="cours_id"]') || null;
                const classe_id = fieldValue(form, 'select[name="classe_id"]') || null;
                const annee_id = fieldValue(form, 'input[name="annee_id"], select[name="annee_id"]') || null;
                const date_absence = dateInput ? dateInput.value : new Date().toISOString().slice(0, 10);
                const motif = fieldValue(form, 'input[name="motif"], textarea[name="motif"]');
                const justifieeInput = form.querySelector('input[name="justifiee"]');
                const justifiee = justifieeInput ? justifieeInput.checked : false;

                if (!eleve_id || !date_absence) {
                    alert('Veuillez sélectionner au moins un élève et une date.');
                    return;
                }

                form.dataset.offlineQueueing = '1';
                try {
                    const studentName = selectedText(eleveSelect, 'Élève');
                    const baseVersionVal = fieldValue(form, 'input[name="base_version"], input[name="sync_version"]');
                    const absIdVal = fieldValue(form, 'input[name="absence_id"], input[name="id"]');
                    const absPayload = {
                        eleve_id: parseInt(eleve_id, 10),
                        cours_id: cours_id ? parseInt(cours_id, 10) : null,
                        classe_id: classe_id ? parseInt(classe_id, 10) : null,
                        annee_id: annee_id ? parseInt(annee_id, 10) : null,
                        date_absence: date_absence,
                        motif: motif,
                        justifiee: justifiee
                    };
                    if (baseVersionVal) absPayload.base_version = parseInt(baseVersionVal, 10);
                    if (absIdVal) absPayload.absence_id = parseInt(absIdVal, 10);
                    await offlineManager.addToSync('absence', absPayload);

                    showNotification([
                        `Absence de ${studentName} enregistrée sur cet appareil !`,
                        'En attente de synchronisation automatique.'
                    ], 'warning');

                    if (form.querySelector('input[name="motif"]')) {
                        form.querySelector('input[name="motif"]').value = '';
                    }

                    updateSyncBadge();

                } catch (err) {
                    console.error('Erreur enregistrement absence hors-ligne:', err);
                    alert('Erreur de sauvegarde locale: ' + err.message);
                } finally {
                    delete form.dataset.offlineQueueing;
                }
            }
        });
    }

    // 4b. Interception du formulaire des Élèves (Création & Modification)
    function setupElevesOffline() {
        const path = window.location.pathname;

        // Formulaire d'ajout élève
        if (path.includes('/ajouter_eleve') || path.includes('/eleves/ajouter') || path.includes('/eleve/creer')) {
            const form = document.querySelector('form');
            if (!form) return;

            form.addEventListener('submit', async function (e) {
                if (canQueueOffline(form)) {
                    e.preventDefault();

                    const nomInput = form.querySelector('input[name="nom"]');
                    const prenomInput = form.querySelector('input[name="prenom"]');
                    if (!nomInput || !prenomInput || !nomInput.value.trim() || !prenomInput.value.trim()) {
                        alert('Le nom et le prénom sont obligatoires.');
                        return;
                    }

                    const local_student_uuid = (typeof generateUUID === 'function') ? generateUUID() : 'local-' + Date.now();
                    const classeSelect = form.querySelector('select[name="classe_id"]');
                    const classe_id = (classeSelect && classeSelect.value) ? parseInt(classeSelect.value, 10) : null;

                    const payload = {
                        local_student_uuid: local_student_uuid,
                        nom: nomInput.value.trim(),
                        prenom: prenomInput.value.trim(),
                        date_naissance: form.querySelector('input[name="date_naissance"]') ? form.querySelector('input[name="date_naissance"]').value : null,
                        genre: form.querySelector('select[name="genre"]') ? form.querySelector('select[name="genre"]').value : 'M',
                        adresse: form.querySelector('input[name="adresse"]') ? form.querySelector('input[name="adresse"]').value : '',
                        telephone: form.querySelector('input[name="telephone"]') ? form.querySelector('input[name="telephone"]').value : '',
                        contact_parent: form.querySelector('input[name="contact_parent"]') ? form.querySelector('input[name="contact_parent"]').value : '',
                        email_parent: form.querySelector('input[name="email_parent"]') ? form.querySelector('input[name="email_parent"]').value : '',
                        frais_annuels: form.querySelector('input[name="frais_annuels"]') ? parseFloat(form.querySelector('input[name="frais_annuels"]').value) : 150000.0,
                        classe_id: classe_id
                    };

                    form.dataset.offlineQueueing = '1';
                    try {
                        await offlineManager.addToSync('eleve_creation', payload);

                        if (classe_id) {
                            await offlineManager.addToSync('inscription', {
                                local_student_uuid: local_student_uuid,
                                classe_id: classe_id,
                                frais_annuels: payload.frais_annuels
                            });
                        }

                        showNotification([
                            `Élève ${payload.prenom} ${payload.nom} créé localement sur cet appareil !`,
                            "En attente de synchronisation dès le retour d'Internet."
                        ], 'warning');

                        form.reset();
                        updateSyncBadge();

                    } catch (err) {
                        console.error('Erreur enregistrement élève hors-ligne:', err);
                        alert('Erreur de sauvegarde locale: ' + err.message);
                    } finally {
                        delete form.dataset.offlineQueueing;
                    }
                }
            });
        }

        // Formulaire de modification élève
        if (path.includes('/modifier_eleve') || path.includes('/eleves/modifier') || path.includes('/eleve/editer')) {
            const form = document.querySelector('form');
            if (!form) return;

            form.addEventListener('submit', async function (e) {
                if (canQueueOffline(form)) {
                    e.preventDefault();

                    const eleveIdInput = form.querySelector('input[name="eleve_id"], input[name="id"]');
                    const eleveId = eleveIdInput ? parseInt(eleveIdInput.value, 10) : null;
                    const nomInput = form.querySelector('input[name="nom"]');
                    const prenomInput = form.querySelector('input[name="prenom"]');
                    const baseVersionVal = form.querySelector('input[name="base_version"], input[name="sync_version"]');

                    if (!eleveId || !nomInput || !prenomInput) {
                        alert('Données d\'élève incomplètes pour la modification.');
                        return;
                    }

                    const payload = {
                        eleve_id: eleveId,
                        nom: nomInput.value.trim(),
                        prenom: prenomInput.value.trim(),
                        genre: form.querySelector('select[name="genre"]') ? form.querySelector('select[name="genre"]').value : 'M',
                        adresse: form.querySelector('input[name="adresse"]') ? form.querySelector('input[name="adresse"]').value : '',
                        telephone: form.querySelector('input[name="telephone"]') ? form.querySelector('input[name="telephone"]').value : '',
                        contact_parent: form.querySelector('input[name="contact_parent"]') ? form.querySelector('input[name="contact_parent"]').value : '',
                        email_parent: form.querySelector('input[name="email_parent"]') ? form.querySelector('input[name="email_parent"]').value : ''
                    };
                    if (baseVersionVal && baseVersionVal.value) {
                        payload.base_version = parseInt(baseVersionVal.value, 10);
                    }

                    form.dataset.offlineQueueing = '1';
                    try {
                        await offlineManager.addToSync('eleve_modification', payload);
                        showNotification([
                            `Modifications pour ${payload.prenom} ${payload.nom} enregistrées sur cet appareil !`,
                            'En attente de synchronisation.'
                        ], 'warning');
                        updateSyncBadge();
                    } catch (err) {
                        console.error('Erreur modification élève hors-ligne:', err);
                        alert('Erreur de sauvegarde locale: ' + err.message);
                    } finally {
                        delete form.dataset.offlineQueueing;
                    }
                }
            });
        }
    }

    // 4c. Verrouillage strict des opérations réservées Internet Obligatoire (ONLINE ONLY)
    function setupOnlineOnlyRestrictions() {
        const path = window.location.pathname;
        const onlineOnlyKeywords = [
            '/cours',
            '/paiement',
            '/paiements',
            '/eleve',
            '/eleves',
            '/ajouter_eleve',
            '/modifier_eleve',
            '/annees-scolaires',
            '/annee_scolaire',
            '/niveaux',
            '/utilisateurs',
            '/utilisateur',
            '/super_admin',
            '/backup',
            '/restore'
        ];

        const isOnlineOnlyPage = onlineOnlyKeywords.some(kw => path.includes(kw));

        if (isOnlineOnlyPage) {
            const forms = document.querySelectorAll('form');
            forms.forEach(f => {
                f.addEventListener('submit', function (e) {
                    if (!navigator.onLine) {
                        e.preventDefault();
                        const message = offlineRequiredMessage(path);
                        showNotification(message, 'danger');
                        alert(message);
                    }
                });
            });
        }
    }

    // 5. Remplissage des formulaires si la page a été chargée hors-ligne
    async function populateOfflineOptions() {
        if (navigator.onLine) return;

        let cacheKey;
        if (typeof offlineManager !== 'undefined' && typeof offlineManager.isAdmin === 'function' && offlineManager.isAdmin()) {
            cacheKey = offlineDB.getAdminCacheKey();
        } else {
            cacheKey = offlineDB.getTeacherCacheKey();
        }

        let cached = await offlineDB.getCachedData(cacheKey);
        if (!cached && typeof offlineDB.getAdminCacheKey === 'function' && typeof offlineDB.getTeacherCacheKey === 'function') {
            const altKey = (cacheKey === offlineDB.getAdminCacheKey())
                ? offlineDB.getTeacherCacheKey()
                : offlineDB.getAdminCacheKey();
            cached = await offlineDB.getCachedData(altKey);
        }
        if (!cached) return;

        // Remplir classes si vide
        const classeSelect = document.querySelector('select[name="classe_id"]');
        if (classeSelect && classeSelect.options.length <= 1 && cached.classes) {
            classeSelect.innerHTML = '<option value="">Choisir une classe...</option>';
            cached.classes.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = c.nom;
                classeSelect.appendChild(opt);
            });
        }

        // Remplir cours si vide
        const coursSelect = document.querySelector('select[name="cours_id"]');
        if (coursSelect && coursSelect.options.length <= 1 && cached.cours) {
            coursSelect.innerHTML = '<option value="">Choisir un cours...</option>';
            cached.cours.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                const teacherInfo = c.professeur_nom ? ` (${c.professeur_nom})` : '';
                opt.textContent = `${c.nom} (${c.classe_nom || 'Sans classe'})${teacherInfo}`;
                coursSelect.appendChild(opt);
            });
        }

        // Remplir élèves si vide
        const eleveSelect = document.querySelector('select[name="eleve_id"]');
        if (eleveSelect && eleveSelect.options.length <= 1 && cached.eleves) {
            eleveSelect.innerHTML = '<option value="">Choisir un élève...</option>';
            cached.eleves.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.id;
                opt.textContent = `${e.prenom} ${e.nom} - ${e.classe_nom || 'Sans classe'}`;
                eleveSelect.appendChild(opt);
            });
        }
    }

    // 6. Protection de déconnexion (Logout modal si pending > 0 ou conflits > 0)
    function setupLogoutProtection() {
        const logoutLinks = document.querySelectorAll('a[href*="logout"]');
        if (!logoutLinks.length) return;

        logoutLinks.forEach(link => {
            link.addEventListener('click', async function (e) {
                if (typeof offlineDB === 'undefined' || link.id === 'klasoraConfirmLogoutBtn') return;

                e.preventDefault();

                let stats;
                try {
                    stats = await offlineDB.getStats();
                } catch (error) {
                    window.location.href = link.href;
                    return;
                }

                if (stats && stats.total > 0) {
                    const countText = document.getElementById('klasoraPendingCountText');
                    if (countText) {
                        let textDesc = `${stats.total}`;
                        if (stats.conflicts > 0) {
                            textDesc = `${stats.total} (dont ⚠️ ${stats.conflicts} conflit(s) à résoudre)`;
                        }
                        countText.textContent = textDesc;
                    }

                    const modalEl = document.getElementById('klasoraLogoutWarningModal');
                    if (modalEl && typeof bootstrap !== 'undefined') {
                        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
                        modal.show();
                    } else {
                        let msg = `Attention : Vous avez ${stats.total} opération(s) locale(s) non synchronisée(s)`;
                        if (stats.conflicts > 0) {
                            msg += ` dont ${stats.conflicts} conflit(s) à résoudre`;
                        }
                        msg += `.\n\nVoulez-vous vraiment vous déconnecter `;
                        const confirmLogout = confirm(msg);
                        if (confirmLogout) {
                            if (window.klasoraPwaCleanOnLogout) window.klasoraPwaCleanOnLogout();
                            window.location.href = link.href;
                        }
                    }
                } else {
                    if (window.klasoraPwaCleanOnLogout) window.klasoraPwaCleanOnLogout();
                    window.location.href = link.href;
                }
            });
        });

        const stayBtn = document.getElementById('klasoraStayAndSyncBtn');
        if (stayBtn) {
            stayBtn.addEventListener('click', () => {
                if (offlineManager) offlineManager.sync(true);
            });
        }
    }

    // 7. Initialisation globale
    document.addEventListener('DOMContentLoaded', () => {
        updateSyncBadge();
        setupNotesOffline();
        setupAbsencesOffline();
        setupOnlineOnlyRestrictions();
        setupLogoutProtection();
        populateOfflineOptions();

        // Événements OfflineManager
        if (typeof offlineManager !== 'undefined') {
            offlineManager.on('sync-start', updateSyncBadge);
            offlineManager.on('sync-end', updateSyncBadge);
            offlineManager.on('sync-success', (data) => {
                updateSyncBadge();
                if (data.count > 0) {
                    showNotification(`✅ Synchronisation réussie : ${data.count} opération(s) transmise(s) au serveur.`, 'success');
                }
            });
            offlineManager.on('sync-conflict', (data) => {
                updateSyncBadge();
                showNotification(`⚠️ Conflit détecté lors de la synchronisation : ${data.message}`, 'warning');
            });
            offlineManager.on('sync-auth-required', () => {
                showNotification(`⚠️ Session expirée. Veuillez vous reconnecter pour finaliser la synchronisation. Vos saisies locales sont conservées en sécurité.`, 'danger');
            });
            offlineManager.on('sync-forbidden', (data) => {
                updateSyncBadge();
                showNotification(`⛔ ${data.message || "Vous n'avez plus l'autorisation de modifier cette donnée."}`, 'danger');
            });
        }

        window.addEventListener('online', updateSyncBadge);
        window.addEventListener('offline', updateSyncBadge);

        // Clic sur le badge de sync pour forcer la synchronisation manuelle
        const badge = document.getElementById('klasoraSyncStatusBadge');
        if (badge) {
            badge.addEventListener('click', () => {
                if (offlineManager) {
                    offlineManager.sync(true);
                }
            });
        }
    });

})();
