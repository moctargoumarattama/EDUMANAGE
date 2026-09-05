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

    // 2. Auto-hide des alertes après 5 secondes
    const alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = bootstrap.Alert.getInstance(alert);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 5000);
    });

    // 3. Validation Bootstrap standard pour les formulaires .needs-validation
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

    // 4. Calcul de moyenne (aide réutilisable)
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
