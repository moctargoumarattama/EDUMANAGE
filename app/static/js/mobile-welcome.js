(function () {
    'use strict';

    const MOBILE_QUERY = '(max-width: 768px)';
    const DISPLAY_MS = 3000;

    document.addEventListener('DOMContentLoaded', function () {
        const toast = document.querySelector('[data-mobile-welcome-toast]');
        if (!toast || !window.matchMedia(MOBILE_QUERY).matches) return;

        const role = toast.dataset.role || 'user';
        if (!['admin', 'super_admin'].includes(role)) return;

        const key = `klasora_admin_welcome_shown_${role}`;
        if (sessionStorage.getItem(key) === '1') return;

        sessionStorage.setItem(key, '1');
        toast.classList.add('is-visible');

        window.setTimeout(function () {
            toast.classList.add('is-hiding');
            window.setTimeout(function () {
                toast.classList.remove('is-visible', 'is-hiding');
            }, 260);
        }, DISPLAY_MS);
    });
}());
