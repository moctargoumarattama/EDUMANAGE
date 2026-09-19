// static/js/offline-forms.js - Gestionnaire de formulaires hors-ligne & UI KLASORA V2

(function () {
    'use strict';

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
        alertEl.innerHTML = `
            <div class="d-flex align-items-center">
                <i class="fas ${type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'} fa-lg me-2"></i>
                <div>${message}</div>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Fermer"></button>
        `;

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

        const form = document.querySelector('form[action*="notes"]') || document.querySelector('form');
        if (!form) return;

        const valeurInput = form.querySelector('input[name="valeur"]');
        const eleveSelect = form.querySelector('select[name="eleve_id"]');
        const coursSelect = form.querySelector('select[name="cours_id"]');

        if (!valeurInput || !eleveSelect) return;

        form.addEventListener('submit', async function (e) {
            if (!navigator.onLine) {
                e.preventDefault();

                const eleve_id = eleveSelect.value;
                const cours_id = coursSelect ? coursSelect.value : null;
                const valeur = valeurInput.value;
                const type_eval = form.querySelector('select[name="type_evaluation"]').value || 'Devoir';
                const coef = form.querySelector('input[name="coefficient"]').value || 1.0;
                const periodeSelect = form.querySelector('select[name="periode"]');
                const anneeSelect = form.querySelector('select[name="annee_id"]');
                const periode = (periodeSelect && periodeSelect.value)
                    || (anneeSelect && anneeSelect.selectedOptions[0] ? anneeSelect.selectedOptions[0].text : '');
                const date_eval = form.querySelector('input[name="date_evaluation"]').value || new Date().toISOString();

                if (!eleve_id || !cours_id || valeur === '') {
                    alert('Veuillez remplir les champs obligatoires (Élève, Cours, Note).');
                    return;
                }

                try {
                    const studentName = eleveSelect.selectedOptions[0] ? eleveSelect.selectedOptions[0].text : 'Élève';
                    const baseVersionInput = form.querySelector('input[name="base_version"], input[name="sync_version"]');
                    const noteIdInput = form.querySelector('input[name="note_id"], input[name="id"]');
                    const baseVersionVal = baseVersionInput ? baseVersionInput.value : '';
                    const noteIdVal = noteIdInput ? noteIdInput.value : '';
                    const notePayload = {
                        eleve_id: parseInt(eleve_id, 10),
                        cours_id: parseInt(cours_id, 10),
                        valeur: parseFloat(valeur),
                        type_evaluation: type_eval,
                        coefficient: parseFloat(coef),
                        periode: periode,
                        date_evaluation: date_eval
                    };
                    if (baseVersionVal) notePayload.base_version = parseInt(baseVersionVal, 10);
                    if (noteIdVal) notePayload.note_id = parseInt(noteIdVal, 10);
                    await offlineManager.addToSync('note', notePayload);

                    showNotification(`✅ Note de <strong>${valeur}/20</strong> pour <strong>${studentName}</strong> enregistrée localement sur cet appareil !<br><small class="text-muted">🟠 En attente de synchronisation dès le retour d'Internet.</small>`, 'warning');

                    // Réinitialiser le champ note pour la saisie suivante
                    valeurInput.value = '';
                    valeurInput.focus();

                    updateSyncBadge();

                } catch (err) {
                    console.error('Erreur enregistrement note hors-ligne:', err);
                    alert('Erreur de sauvegarde locale: ' + err.message);
                }
            }
        });
    }

    // 4. Interception du formulaire des Absences
    function setupAbsencesOffline() {
        const path = window.location.pathname;
        if (!path.includes('/absences')) return;

        const form = document.querySelector('form[action*="absences"]') || document.querySelector('form');
        if (!form) return;

        const eleveSelect = form.querySelector('select[name="eleve_id"]');
        const dateInput = form.querySelector('input[name="date_absence"]');

        if (!eleveSelect) return;

        form.addEventListener('submit', async function (e) {
            if (!navigator.onLine) {
                e.preventDefault();

                const eleve_id = eleveSelect.value;
                const cours_id = form.querySelector('select[name="cours_id"]').value || null;
                const date_absence = dateInput ? dateInput.value : new Date().toISOString().slice(0, 10);
                const motif = form.querySelector('input[name="motif"], textarea[name="motif"]').value || '';
                const justifiee = form.querySelector('input[name="justifiee"]').checked || false;

                if (!eleve_id || !date_absence) {
                    alert('Veuillez sélectionner au moins un élève et une date.');
                    return;
                }

                try {
                    const studentName = eleveSelect.selectedOptions[0] ? eleveSelect.selectedOptions[0].text : 'Élève';
                    const baseVersionInput = form.querySelector('input[name="base_version"], input[name="sync_version"]');
                    const absIdInput = form.querySelector('input[name="absence_id"], input[name="id"]');
                    const baseVersionVal = baseVersionInput ? baseVersionInput.value : '';
                    const absIdVal = absIdInput ? absIdInput.value : '';
                    const absPayload = {
                        eleve_id: parseInt(eleve_id, 10),
                        cours_id: cours_id ? parseInt(cours_id, 10) : null,
                        date_absence: date_absence,
                        motif: motif,
                        justifiee: justifiee
                    };
                    if (baseVersionVal) absPayload.base_version = parseInt(baseVersionVal, 10);
                    if (absIdVal) absPayload.absence_id = parseInt(absIdVal, 10);
                    await offlineManager.addToSync('absence', absPayload);

                    showNotification(`✅ Absence de <strong>${studentName}</strong> enregistrée sur cet appareil !<br><small class="text-muted">🟠 En attente de synchronisation automatique.</small>`, 'warning');

                    if (form.querySelector('input[name="motif"]')) {
                        form.querySelector('input[name="motif"]').value = '';
                    }

                    updateSyncBadge();

                } catch (err) {
                    console.error('Erreur enregistrement absence hors-ligne:', err);
                    alert('Erreur de sauvegarde locale: ' + err.message);
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
                if (!navigator.onLine) {
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

                    try {
                        await offlineManager.addToSync('eleve_creation', payload);

                        if (classe_id) {
                            await offlineManager.addToSync('inscription', {
                                local_student_uuid: local_student_uuid,
                                classe_id: classe_id,
                                frais_annuels: payload.frais_annuels
                            });
                        }

                        showNotification(`✅ Élève <strong>${payload.prenom} ${payload.nom}</strong> créé localement sur cet appareil !<br><small class="text-muted">🟠 En attente de synchronisation dès le retour d'Internet.</small>`, 'warning');

                        form.reset();
                        updateSyncBadge();

                    } catch (err) {
                        console.error('Erreur enregistrement élève hors-ligne:', err);
                        alert('Erreur de sauvegarde locale: ' + err.message);
                    }
                }
            });
        }

        // Formulaire de modification élève
        if (path.includes('/modifier_eleve') || path.includes('/eleves/modifier') || path.includes('/eleve/editer')) {
            const form = document.querySelector('form');
            if (!form) return;

            form.addEventListener('submit', async function (e) {
                if (!navigator.onLine) {
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

                    try {
                        await offlineManager.addToSync('eleve_modification', payload);
                        showNotification(`✅ Modifications pour <strong>${payload.prenom} ${payload.nom}</strong> enregistrées sur cet appareil !<br><small class="text-muted">🟠 En attente de synchronisation.</small>`, 'warning');
                        updateSyncBadge();
                    } catch (err) {
                        console.error('Erreur modification élève hors-ligne:', err);
                        alert('Erreur de sauvegarde locale: ' + err.message);
                    }
                }
            });
        }
    }

    // 4c. Verrouillage strict des opérations réservées Internet Obligatoire (ONLINE ONLY)
    function setupOnlineOnlyRestrictions() {
        const path = window.location.pathname;
        const onlineOnlyKeywords = [
            '/paiement',
            '/paiements',
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
                        showNotification('⛔ <strong>Connexion Internet requise</strong> pour cette opération (Finances / Structure / Sécurité en ligne uniquement).', 'danger');
                        alert('Connexion Internet requise pour cette opération.');
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
                if (typeof offlineDB === 'undefined') return;

                const stats = await offlineDB.getStats();
                if (stats && stats.total > 0) {
                    e.preventDefault();

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
        setupElevesOffline();
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
