import unittest
from datetime import date, datetime
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Paiement, Absence, Note, Utilisateur
from app.services import generer_alertes_automatiques


class BackendAlertesJamaisPayeConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-backend-alertes-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestBackendAlertesJamaisPaye(unittest.TestCase):
    def setUp(self):
        self.app = create_app(BackendAlertesJamaisPayeConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Test Alertes Jamais Payé",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # Année scolaire couvrant aujourd'hui
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        self.classe = Classe(
            nom="6ème Vigilance",
            niveau="6eme",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.flush()

        self.admin = Utilisateur(
            email="admin_vigilance@test.com",
            nom="Admin",
            prenom="Directeur",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        self.admin.mot_de_passe = generate_password_hash("Admin1234!")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_detection_jamais_paye_vs_retard_partiel(self):
        """Vérifie que 'jamais_paye' vaut True pour 0 versement et False pour un acompte partiel."""
        # Élève A : Jamais payé (0 versement)
        eleve_a = Eleve(
            nom="KOUASSI",
            prenom="Amina",
            date_naissance=date(2013, 1, 1),
            ecole_id=self.ecole.id,
        )
        db.session.add(eleve_a)
        db.session.flush()

        ins_a = Inscription(
            eleve_id=eleve_a.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
        )
        db.session.add(ins_a)

        # Élève B : Acompte partiel (a déjà versé 50 000 FCFA mais reste en retard)
        eleve_b = Eleve(
            nom="DIOP",
            prenom="Moussa",
            date_naissance=date(2013, 2, 2),
            ecole_id=self.ecole.id,
        )
        db.session.add(eleve_b)
        db.session.flush()

        ins_b = Inscription(
            eleve_id=eleve_b.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
        )
        db.session.add(ins_b)
        db.session.flush()

        paiement_partiel = Paiement(
            inscription_id=ins_b.id,
            eleve_id=eleve_b.id,
            montant=50000.0,
            mois="Septembre",
            statut="valide",
            date_paiement=datetime(2025, 9, 15),
            ecole_id=self.ecole.id,
        )
        db.session.add(paiement_partiel)
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole.id, annee=self.annee)
        paiement_alertes = [a for a in alertes if a['source'] == 'Paiements']

        # Trouver alerte A et alerte B
        alerte_a = next((a for a in paiement_alertes if a['eleve_id'] == eleve_a.id), None)
        alerte_b = next((a for a in paiement_alertes if a['eleve_id'] == eleve_b.id), None)

        self.assertIsNotNone(alerte_a, "L'élève sans aucun paiement doit déclencher une alerte")
        self.assertTrue(alerte_a['jamais_paye'], "L'élève avec 0 paiement doit avoir jamais_paye == True")
        self.assertEqual(alerte_a['type'], 'danger')
        self.assertEqual(alerte_a['priorite'], 3)
        self.assertIn("Jamais payé", alerte_a['titre'])

        self.assertIsNotNone(alerte_b, "L'élève avec retard partiel doit déclencher une alerte")
        self.assertFalse(alerte_b['jamais_paye'], "L'élève ayant versé 50 000 FCFA doit avoir jamais_paye == False")
        self.assertNotIn("Jamais payé", alerte_b['titre'])

    def test_deduplication_alertes_strictes(self):
        """Vérifie qu'il n'y a aucune alerte en double pour une même inscription/élève."""
        eleve = Eleve(
            nom="TRAORE",
            prenom="Fatou",
            date_naissance=date(2013, 3, 3),
            ecole_id=self.ecole.id,
        )
        db.session.add(eleve)
        db.session.flush()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
        )
        db.session.add(ins)
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole.id, annee=self.annee)
        alert_ids = [a['id'] for a in alertes]
        self.assertEqual(len(alert_ids), len(set(alert_ids)), "Aucun ID d'alerte ne doit être dupliqué")

        # Vérifier aussi qu'il n'y a pas 2 alertes Paiements pour le même élève
        eleve_paiements = [a for a in alertes if a['eleve_id'] == eleve.id and a['source'] == 'Paiements']
        self.assertLessEqual(len(eleve_paiements), 1, "Un élève ne doit avoir qu'une seule alerte de paiement")

    def test_api_alertes_count_endpoint(self):
        """Vérifie les endpoints /api/alertes et /api/alertes/count avec le comptage jamais_paye."""
        eleve = Eleve(
            nom="BAMBA",
            prenom="Sekou",
            date_naissance=date(2013, 4, 4),
            ecole_id=self.ecole.id,
        )
        db.session.add(eleve)
        db.session.flush()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
        )
        db.session.add(ins)
        db.session.commit()

        # Connexion admin
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id

        resp = self.client.get('/api/alertes/count')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('count', data)
        self.assertIn('jamais_paye', data)
        self.assertGreaterEqual(data['count'], 1)
        self.assertGreaterEqual(data['jamais_paye'], 1)

        resp_list = self.client.get('/api/alertes')
        self.assertEqual(resp_list.status_code, 200)
        data_list = resp_list.get_json()
        self.assertIn('alertes', data_list)
        self.assertIn('stats', data_list)
        self.assertIn('jamais_paye', data_list['stats'])


if __name__ == '__main__':
    unittest.main()
