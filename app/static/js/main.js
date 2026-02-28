// app/static/js/main.js
document.addEventListener('DOMContentLoaded', function() {
    // Activer les tooltips de Bootstrap
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl)
    });
    
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
    
    // Confirmation pour les actions de suppression
    const deleteButtons = document.querySelectorAll('.btn-danger');
    deleteButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm('Êtes-vous sûr de vouloir supprimer cet élément ?')) {
                e.preventDefault();
            }
        });
    });
    
    // Fonction pour calculer automatiquement les moyennes
    window.calculerMoyenne = function(notes, coefficients) {
        let total = 0;
        let totalCoeff = 0;
        
        notes.forEach((note, index) => {
            if (note && coefficients[index]) {
                total += parseFloat(note) * parseFloat(coefficients[index]);
                totalCoeff += parseFloat(coefficients[index]);
            }
        });
        
        return totalCoeff > 0 ? (total / totalCoeff).toFixed(2) : '0.00';
    };
});







