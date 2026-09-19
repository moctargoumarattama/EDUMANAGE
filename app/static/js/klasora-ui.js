(function () {
    'use strict';

    const DEFAULT_TIMEOUT = 15000;

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="csrf_token"]');
        return input ? input.value : '';
    }

    function ensureToastRoot() {
        let root = document.querySelector('.klasora-toast-root');
        if (!root) {
            root = document.createElement('div');
            root.className = 'klasora-toast-root';
            root.setAttribute('aria-live', 'polite');
            document.body.appendChild(root);
        }
        return root;
    }

    function showToast(message, type) {
        const root = ensureToastRoot();
        const toast = document.createElement('div');
        toast.className = `klasora-toast klasora-toast-${type || 'info'}`;
        toast.textContent = message || 'Action effectuée.';
        root.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('is-visible'));
        window.setTimeout(() => {
            toast.classList.remove('is-visible');
            window.setTimeout(() => toast.remove(), 220);
        }, 3000);
    }

    function setButtonLoading(button, loadingText) {
        if (!button) return function () {};
        const originalHtml = button.innerHTML;
        const originalDisabled = button.disabled;
        button.disabled = true;
        button.dataset.klasoraBusy = '1';
        button.innerHTML = `<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>${loadingText || 'Traitement...'}`;
        return function restore() {
            button.innerHTML = originalHtml;
            button.disabled = originalDisabled;
            delete button.dataset.klasoraBusy;
        };
    }

    async function klasoraFetch(url, options) {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), (options && options.timeout) || DEFAULT_TIMEOUT);
        const headers = new Headers((options && options.headers) || {});
        const method = ((options && options.method) || 'GET').toUpperCase();

        headers.set('X-Requested-With', 'XMLHttpRequest');
        headers.set('Accept', 'application/json');
        if (!headers.has('X-CSRFToken')) {
            const token = csrfToken();
            if (token && method !== 'GET') {
                headers.set('X-CSRFToken', token);
                headers.set('X-CSRF-Token', token);
            }
        }

        try {
            const response = await fetch(url, {
                ...options,
                method,
                headers,
                signal: controller.signal
            });

            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                throw new Error('Réponse non compatible AJAX.');
            }
            const data = await response.json();

            if (!response.ok) {
                const message = data.message || data.error || messageForStatus(response.status);
                const error = new Error(message);
                error.status = response.status;
                error.data = data;
                throw error;
            }

            return data;
        } catch (error) {
            if (error.name === 'AbortError') {
                throw new Error('La requête prend trop de temps. Réessayez.');
            }
            if (!navigator.onLine) {
                throw new Error('Connexion indisponible. Réessayez quand le réseau revient.');
            }
            throw error;
        } finally {
            window.clearTimeout(timeout);
        }
    }

    function messageForStatus(status) {
        const messages = {
            400: 'Demande incorrecte.',
            401: 'Session expirée. Reconnectez-vous.',
            403: 'Action non autorisée.',
            404: 'Élément introuvable.',
            409: 'Conflit détecté. Actualisez les données concernées.',
            422: 'Veuillez vérifier les champs.',
            429: 'Trop de tentatives. Patientez puis réessayez.',
            500: 'Erreur serveur. Réessayez plus tard.'
        };
        return messages[status] || 'Action impossible.';
    }

    function removeElementAnimated(target) {
        const el = typeof target === 'string' ? document.querySelector(target) : target;
        if (!el) return;
        el.classList.add('klasora-removing');
        window.setTimeout(() => el.remove(), 230);
    }

    function updateCounter(selector, delta) {
        if (!selector) return;
        document.querySelectorAll(selector).forEach((el) => {
            const current = parseInt(el.textContent, 10);
            if (!Number.isNaN(current)) el.textContent = Math.max(0, current + delta);
        });
    }

    function statusBadge(status) {
        const normalized = status || '';
        if (normalized === 'actif') return '<span class="badge bg-success">actif</span>';
        if (normalized === 'bloque') return '<span class="badge bg-warning text-dark">bloqué</span>';
        if (normalized === 'nouveau') return '<span class="badge bg-danger-subtle text-danger border border-danger-subtle">Nouveau</span>';
        if (normalized === 'en_cours') return '<span class="badge bg-warning-subtle text-warning border border-warning-subtle">En cours</span>';
        if (normalized === 'resolu') return '<span class="badge bg-success-subtle text-success border border-success-subtle">Résolu</span>';
        return `<span class="badge bg-light text-dark border">${normalized}</span>`;
    }

    document.addEventListener('click', async function (event) {
        const action = event.target.closest('[data-klasora-action]');
        if (!action || action.dataset.klasoraBusy === '1') return;

        const url = action.dataset.url || action.getAttribute('href');
        const method = action.dataset.method || 'POST';
        const confirmText = action.dataset.confirm;
        if (!url) return;
        if (confirmText && !window.confirm(confirmText)) {
            event.preventDefault();
            return;
        }

        event.preventDefault();
        const restore = setButtonLoading(action, action.dataset.loading || 'Traitement...');

        try {
            const payload = action.dataset.payload ? JSON.parse(action.dataset.payload) : null;
            const data = await klasoraFetch(url, {
                method,
                headers: payload ? { 'Content-Type': 'application/json' } : {},
                body: payload ? JSON.stringify(payload) : null
            });

            if (data.success === false) throw new Error(data.message || 'Action impossible.');

            if (action.dataset.removeTarget) removeElementAnimated(action.dataset.removeTarget);
            if (action.dataset.counter) updateCounter(action.dataset.counter, parseInt(action.dataset.counterDelta || '-1', 10));
            if (action.dataset.statusTarget && data.new_status) {
                const target = document.querySelector(action.dataset.statusTarget);
                if (target) target.innerHTML = statusBadge(data.new_status);
            }
            if (action.dataset.currentStatus && data.new_status) {
                action.dataset.currentStatus = data.new_status;
            }

            action.dispatchEvent(new CustomEvent('klasora:success', { bubbles: true, detail: data }));
            showToast(data.message || action.dataset.success || 'Action effectuée.', 'success');
        } catch (error) {
            showToast(error.message || 'Action impossible.', error.status === 403 ? 'warning' : 'danger');
        } finally {
            restore();
        }
    });

    document.addEventListener('submit', async function (event) {
        const form = event.target.closest('form[data-klasora-form]');
        if (!form || form.dataset.klasoraBusy === '1') return;
        if (form.dataset.klasoraAjax !== 'true') return;

        const confirmText = form.dataset.confirm;
        if (confirmText && !window.confirm(confirmText)) {
            event.preventDefault();
            return;
        }

        event.preventDefault();
        const submitter = event.submitter || form.querySelector('[type="submit"], button:not([type])');
        const restore = setButtonLoading(submitter, form.dataset.loading || 'Traitement...');
        form.dataset.klasoraBusy = '1';

        try {
            const data = await klasoraFetch(form.action || window.location.href, {
                method: (form.dataset.method || form.method || 'POST').toUpperCase(),
                body: new FormData(form)
            });

            if (data.success === false) throw new Error(data.message || 'Action impossible.');
            if (form.dataset.removeTarget) removeElementAnimated(form.dataset.removeTarget);
            if (form.dataset.removeClosest) {
                const closest = form.closest(form.dataset.removeClosest);
                if (closest) removeElementAnimated(closest);
            }
            if (form.dataset.counter) updateCounter(form.dataset.counter, parseInt(form.dataset.counterDelta || '-1', 10));
            form.dispatchEvent(new CustomEvent('klasora:success', { bubbles: true, detail: data }));
            showToast(data.message || form.dataset.success || 'Action effectuée.', 'success');
        } catch (error) {
            showToast(error.message || 'Action impossible.', error.status === 403 ? 'warning' : 'danger');
        } finally {
            restore();
            delete form.dataset.klasoraBusy;
        }
    });

    window.KlasoraUI = {
        csrfToken,
        fetch: klasoraFetch,
        showToast,
        setButtonLoading,
        removeElementAnimated,
        updateCounter,
        statusBadge
    };
}());
