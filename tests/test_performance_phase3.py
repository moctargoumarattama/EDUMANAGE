import unittest
import re
from app import create_app, db
from sqlalchemy.pool import StaticPool


class Phase3PerformanceTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-phase3-perf"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPerformancePhase3(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Phase3PerformanceTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    def test_base_html_defer_and_fonts(self):
        """Vérifie que base.html utilise defer et préconnecte Google Fonts."""
        with open('app/templates/base.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Google Fonts preconnect et display swap
        self.assertIn('rel="preconnect" href="https://fonts.googleapis.com"', content)
        self.assertIn('rel="preconnect" href="https://fonts.gstatic.com"', content)
        self.assertIn('css2?family=Poppins', content)
        self.assertIn('display=swap', content)

        # Defer sur Bootstrap bundle
        bootstrap_match = re.search(r'<script\s+[^>]*src="[^"]*bootstrap\.bundle\.min\.js"[^>]*>', content)
        self.assertIsNotNone(bootstrap_match, "Bootstrap bundle script non trouvé")
        self.assertIn('defer', bootstrap_match.group(0), "Bootstrap bundle doit avoir l'attribut defer")

        # Defer sur les scripts communs
        for script_name in ['main.js', 'pwa.js', 'db.js', 'offline-manager.js', 'offline-forms.js', 'klasora-ui.js', 'live-filters.js']:
            pattern = rf'<script\s+[^>]*src="[^"]*{script_name}[^"]*"[^>]*>'
            match = re.search(pattern, content)
            self.assertIsNotNone(match, f"Script {script_name} non trouvé dans base.html")
            self.assertIn('defer', match.group(0), f"Script {script_name} doit avoir l'attribut defer")
            self.assertIn("v='14'", match.group(0), f"Script {script_name} doit avoir v='14'")

    def test_service_worker_cache_version_and_purge(self):
        """Vérifie que service-worker.js est synchronisé en v14 et purge les anciens caches."""
        with open('app/static/service-worker.js', 'r', encoding='utf-8') as f:
            content = f.read()

        # Version déclarée
        self.assertIn("CACHE_NAME = 'klasora-cache-v14'", content)
        self.assertIn('/static/css/style.css?v=14', content)

        # Purge des caches obsolètes dans activate
        self.assertIn('!allowedCaches.has(cacheName)', content)
        self.assertIn('caches.delete(cacheName)', content)

    def test_eleves_html_single_dynamic_modal(self):
        """Vérifie que eleves.html utilise une modale unique hors de la boucle pour la suppression."""
        with open('app/templates/eleves.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Présence de la modale unique
        self.assertIn('id="modalSupprimerEleve"', content)
        self.assertIn('id="modalDeleteEleveNom"', content)
        self.assertIn('id="modalDeleteEleveForm"', content)

        # Déclencheurs de modale sur les élèves (pas de form répété dans la boucle)
        self.assertIn('btn-supprimer-eleve', content)
        self.assertIn('data-bs-target="#modalSupprimerEleve"', content)
        self.assertIn('data-action=', content)

        # Absence de formulaire de suppression inline dans la boucle d'élèves
        self.assertNotIn('data-remove-target="#eleve-card-{{ eleve.id }}"', content,
                         "Les formulaires de suppression inline avec token CSRF ne doivent plus être dupliqués par élève")

    def test_eleves_route_per_page_setting(self):
        """Vérifie que la pagination de l'annuaire des élèves est fixée entre 25 et 50."""
        with open('app/routes/eleves.py', 'r', encoding='utf-8') as f:
            content = f.read()

        match = re.search(r'per_page\s*=\s*(\d+)', content)
        self.assertIsNotNone(match, "Variable per_page non trouvée dans app/routes/eleves.py")
        per_page = int(match.group(1))
        self.assertTrue(25 <= per_page <= 50, f"per_page={per_page} doit être compris entre 25 et 50")


    def test_voir_eleve_single_dynamic_modal_and_iife(self):
        """Vérifie que voir_eleve.html utilise une modale unique et un script IIFE."""
        with open('app/templates/voir_eleve.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Présence de la modale unique
        self.assertIn('id="modalMatiereDynamique"', content)
        self.assertIn('id="modalDynMatiereNom"', content)
        self.assertIn('id="modalDynMatiereBody"', content)

        # Absence des modales répétées dans la boucle
        self.assertNotIn('id="modalMatiere_{{ mat_idx }}"', content)
        self.assertNotIn('id="modalMatiere_{{ loop.index }}"', content)

        # Présence des templates inertes
        self.assertIn('id="matieresTemplatesContainer"', content)
        self.assertIn('id="tplMatiereContent_{{ mat_idx }}"', content)

        # Encapsulation IIFE
        self.assertIn("(function() {", content)
        self.assertIn("'use strict';", content)

    def test_demandes_presentation_single_dynamic_modal(self):
        """Vérifie que demandes_presentation.html utilise une modale unique hors boucle."""
        with open('app/templates/admin/demandes_presentation.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Présence de la modale unique
        self.assertIn('id="modalDemandePresentationUnique"', content)
        self.assertIn('id="formDemandePresentation"', content)
        self.assertIn('id="modalDemandePresentationTitle"', content)

        # Absence de la modale répétée dans la boucle
        self.assertNotIn('id="noteModal{{ d.id }}"', content)

        # Déclencheur avec data attributes
        self.assertIn('data-bs-target="#modalDemandePresentationUnique"', content)

        # Encapsulation IIFE
        self.assertIn("(function() {", content)
        self.assertIn("'use strict';", content)

    def test_gestion_annees_single_semestres_modal(self):
        """Vérifie que gestion_annees.html utilise une modale unique pour les semestres."""
        with open('app/templates/gestion_annees.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Présence de la modale unique
        self.assertIn('id="modalSemestresUnique"', content)
        self.assertIn('id="modalSemestresAnneeNom"', content)

        # Absence de la modale répétée dans la boucle
        self.assertNotIn('id="modalSemestres{{ annee.id }}"', content)

        # Encapsulation IIFE
        self.assertIn("(function() {", content)
        self.assertIn("'use strict';", content)

    def test_admin_emplois_iife_encapsulation(self):
        """Vérifie que le script d'admin_emplois.html est encapsulé dans une IIFE."""
        with open('app/templates/admin_emplois.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("(function() {", content)
        self.assertIn("'use strict';", content)
        self.assertIn("})();", content)


if __name__ == '__main__':
    unittest.main()

