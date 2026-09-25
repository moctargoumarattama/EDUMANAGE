import unittest
from datetime import datetime
from app import create_app, db
from app.models import Utilisateur, Ecole, Eleve, Classe, AnneeScolaire, Inscription
from werkzeug.security import generate_password_hash
from sqlalchemy.pool import StaticPool


class AuditAlertesPhase4TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-audit-alertes-phase4"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestAuditAlertesPhase4(unittest.TestCase):
    def setUp(self):
        self.app = create_app(AuditAlertesPhase4TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_alertes_html_mailto_format(self):
        """Vérifie que l'URI mailto dans alertes.html contient bien le délimiteur '?' avant 'subject='."""
        with open('app/templates/alertes.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('mailto:{{ alerte.email_parent }}?subject=', content)
        self.assertNotIn('mailto:{{ alerte.email_parent }}subject=', content)

    def test_generer_alertes_options_uses_selectinload(self):
        """Vérifie que generer_alertes_automatiques emploie selectinload pour les collections 1-N."""
        import inspect
        from app.services import generer_alertes_automatiques

        source = inspect.getsource(generer_alertes_automatiques)
        self.assertIn("selectinload(Inscription.notes)", source)
        self.assertIn("selectinload(Inscription.absences)", source)
        self.assertIn("selectinload(Inscription.paiements)", source)
        self.assertIn("joinedload(Inscription.eleve)", source)
        self.assertIn("joinedload(Inscription.classe)", source)

    def test_alertes_route_no_check_parent_access_loop(self):
        """Vérifie que le filtrage parent dans alertes.py utilise un préchargement O(1)."""
        with open('app/routes/alertes.py', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertNotIn("check_parent_access(a['eleve_id'])", content)
        self.assertIn("mes_eleves_ids = {e.id for e in Eleve.query.filter_by(parent_id=current_user.id, ecole_id=ecole_id).all()}", content)

    def test_session_alertes_traitees_capping(self):
        """Vérifie que la session plafonne les alertes traitées à 100 éléments maximum."""
        # Créer l'école et l'utilisateur
        ecole = Ecole(nom="École Test", onboarding_complete=True)
        db.session.add(ecole)
        db.session.commit()

        annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=datetime(2025, 9, 1),
            date_fin=datetime(2026, 6, 30),
            statut="active",
            ecole_id=ecole.id
        )
        db.session.add(annee)
        db.session.commit()

        user = Utilisateur(
            nom="Admin",
            prenom="Test",
            email="admin_audit@test.com",
            role="admin",
            ecole_id=ecole.id,
            mot_de_passe=generate_password_hash("password123")
        )
        db.session.add(user)
        db.session.commit()

        with self.client:
            # Login
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(user.id)
                sess['_fresh'] = True
                sess['ecole_id'] = ecole.id
                # Pré-remplir la session avec 120 alertes traitées existantes
                sess['alertes_traitees'] = [f"alerte_{i}" for i in range(120)]

            # Marquer une nouvelle alerte comme traitée
            res = self.client.post(
                '/api/alertes/nouvelle_alerte_test/read',
                json={'action': 'toggle'}
            )
            self.assertEqual(res.status_code, 200, f"Got {res.status_code}: {res.get_data(as_text=True)}")

            # Vérifier que le contenu stocké dans la session ne dépasse pas 100 éléments
            with self.client.session_transaction() as sess:
                stored = sess.get('alertes_traitees', [])
                self.assertLessEqual(len(stored), 100)
                self.assertIn('nouvelle_alerte_test', stored)

    def test_alertes_html_iife_and_no_inline_onclick(self):
        """Vérifie l'encapsulation IIFE et l'absence totale d'attributs onclick inline dans alertes.html."""
        with open('app/templates/alertes.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("(function() {", content)
        self.assertIn("'use strict';", content)
        self.assertIn("})();", content)
        self.assertNotIn("onclick=", content)
        self.assertIn('data-action="toggle-alerte"', content)
        self.assertIn('data-alerte-id=', content)

    def test_rapports_cache_code_mort_removed(self):
        """Vérifie que le cache orphelin au niveau module _rapports_cache a été supprimé."""
        with open('app/routes/rapports.py', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertNotIn("_rapports_cache = {", content)

    def test_recherche_inscriptions_eager_loading(self):
        """Vérifie que /recherche utilise l'eager loading sur annee_scolaire, classe et ecole."""
        with open('app/routes/rapports.py', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("db.joinedload(Inscription.annee_scolaire)", content)
        self.assertIn("db.joinedload(Inscription.classe)", content)
        self.assertIn("db.joinedload(Inscription.ecole)", content)

    def test_notifications_parent_grouped_queries(self):
        """Vérifie que /notifications pour les parents utilise des requêtes groupées O(1)."""
        with open('app/routes/rapports.py', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("Note.eleve_id.in_(enfant_ids)", content)
        self.assertIn("Absence.eleve_id.in_(enfant_ids)", content)
        self.assertIn(".group_by(Note.eleve_id)", content)
        self.assertIn(".group_by(Absence.eleve_id)", content)

    def test_no_parent_email_on_creer_note(self):
        """Vérifie que la saisie/création d'une note n'envoie aucun email au parent."""
        import inspect
        import app.services.notes_annuelles as notes_service

        source = inspect.getsource(notes_service)
        self.assertNotIn("envoyer_email", source)
        self.assertNotIn("note_parent_notification", source)


if __name__ == '__main__':
    unittest.main()
