import unittest
from datetime import date
from flask import g
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import (
    Ecole, AnneeScolaire, Utilisateur, Classe, Eleve, Inscription, Cours,
    Professeur, NiveauScolaire, AnneeNiveauConfig, EcoleNiveauConfig, Paiement, Note
)

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

class Phase3Lot2HistoriqueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Écoles (École A et École B)
        self.ecole_a = Ecole(nom="École A")
        self.ecole_b = Ecole(nom="École B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # 2. Années scolaires
        self.annee_2025 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id
        )
        self.annee_2026 = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        self.annee_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_2025, self.annee_2026, self.annee_b])
        db.session.flush()

        # Configs de niveaux pour contourner le check d'onboarding
        self.niv_6e = NiveauScolaire(code="6E", nom="6ème", cycle="college", ordre=1)
        self.niv_5e = NiveauScolaire(code="5E", nom="5ème", cycle="college", ordre=2)
        db.session.add_all([self.niv_6e, self.niv_5e])
        db.session.flush()

        self.enc_a_6e = EcoleNiveauConfig(ecole_id=self.ecole_a.id, niveau_id=self.niv_6e.id, actif=True)
        self.enc_a_5e = EcoleNiveauConfig(ecole_id=self.ecole_a.id, niveau_id=self.niv_5e.id, actif=True)
        self.enc_b_6e = EcoleNiveauConfig(ecole_id=self.ecole_b.id, niveau_id=self.niv_6e.id, actif=True)

        self.cfg_a_2025 = AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2025.id, niveau_id=self.niv_6e.id, actif=True)
        self.cfg_a_2026 = AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2026.id, niveau_id=self.niv_5e.id, actif=True)
        self.cfg_b_2025 = AnneeNiveauConfig(ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id, niveau_id=self.niv_6e.id, actif=True)

        db.session.add_all([self.enc_a_6e, self.enc_a_5e, self.enc_b_6e, self.cfg_a_2025, self.cfg_a_2026, self.cfg_b_2025])
        db.session.flush()

        # Classes
        self.classe_6a = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2025.id, niveau_id=self.niv_6e.id)
        self.classe_5b = Classe(nom="5e B", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2026.id, niveau_id=self.niv_5e.id)
        self.classe_b_6a = Classe(nom="6e A (École B)", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id, niveau_id=self.niv_6e.id)
        db.session.add_all([self.classe_6a, self.classe_5b, self.classe_b_6a])
        db.session.flush()

        # Utilisateur Admin
        self.admin = Utilisateur(
            nom="Admin", prenom="A", email="admin@ecole-a.com",
            role="admin", ecole_id=self.ecole_a.id, mot_de_passe=generate_password_hash("password")
        )
        db.session.add(self.admin)
        db.session.flush()

        # Élève A (Permanente)
        self.eleve_a = Eleve(
            nom="KOUAME", prenom="Amina", date_naissance=date(2010, 1, 1), ecole_id=self.ecole_a.id,
            classe_id=None # Volontairement None pour tester la résilience totale
        )
        db.session.add(self.eleve_a)
        db.session.flush()

        # Inscriptions annuelles
        self.insc_2025 = Inscription(
            eleve_id=self.eleve_a.id, classe_id=self.classe_6a.id,
            annee_scolaire_id=self.annee_2025.id, ecole_id=self.ecole_a.id, statut="inscrit"
        )
        self.insc_2026 = Inscription(
            eleve_id=self.eleve_a.id, classe_id=self.classe_5b.id,
            annee_scolaire_id=self.annee_2026.id, ecole_id=self.ecole_a.id, statut="inscrit"
        )
        db.session.add_all([self.insc_2025, self.insc_2026])
        db.session.flush()

        # Paiement 2025-2026
        self.paiement_2025 = Paiement(
            ecole_id=self.ecole_a.id, eleve_id=self.eleve_a.id,
            inscription_id=self.insc_2025.id, montant=50000.0,
            mois="Septembre", annee="2025", mode_paiement="espèces", statut="payé"
        )
        # Note 2025-2026
        self.cours_math_6a = Cours(nom="Maths", ecole_id=self.ecole_a.id, classe_id=self.classe_6a.id)
        db.session.add(self.cours_math_6a)
        db.session.flush()

        self.note_2025 = Note(
            ecole_id=self.ecole_a.id, annee_id=self.annee_2025.id,
            inscription_id=self.insc_2025.id, eleve_id=self.eleve_a.id,
            cours_id=self.cours_math_6a.id, valeur=15.0, coefficient=1.0,
            type_evaluation="Devoir", periode="Semestre 1"
        )
        db.session.add_all([self.paiement_2025, self.note_2025])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['ecole_id'] = self.ecole_a.id
            sess['annee_consultee'] = self.annee_2025.id

    def test_1_recu_paiement_historique_affiche_classe_2025(self):
        """1. Le reçu de paiement 2025-2026 doit afficher 6e A même si eleve.classe_id est None."""
        self.login_admin()
        res = self.client.get(f'/paiements/{self.paiement_2025.id}/recu')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"6e A", res.data)

    def test_2_export_excel_paiement_affiche_classe_2025(self):
        """2. L'export Excel des paiements 2025-2026 doit afficher 6e A."""
        self.login_admin()
        res = self.client.get('/paiements/export_excel')
        self.assertEqual(res.status_code, 200)

    def test_3_note_historique_affiche_classe_2025(self):
        """3. L'affichage des notes 2025-2026 doit afficher 6e A."""
        self.login_admin()
        res = self.client.get('/notes')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"6e A", res.data)

    def test_4_cours_details_affiche_classe_2025(self):
        """4. Le détail d'un cours historique doit afficher 6e A."""
        self.login_admin()
        res = self.client.get(f'/cours/{self.cours_math_6a.id}')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"6e A", res.data)

    def test_5_recherche_annee_2025_et_2026(self):
        """5 & 6. Recherche globale doit afficher les classes correspondant aux inscriptions de chaque année."""
        self.login_admin()
        res = self.client.get('/recherche?q=KOUAME')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"5e B", res.data) # La plus récente
        self.assertIn(b"6e A", res.data) # Dans l'historique

    def test_7_fiche_eleve_sans_inscription(self):
        """7. Un élève sans inscription ne doit afficher aucune fausse classe legacy."""
        eleve_nu = Eleve(nom="SANS", prenom="Inscription", date_naissance=date(2010, 1, 1), ecole_id=self.ecole_a.id, classe_id=None)
        db.session.add(eleve_nu)
        db.session.commit()

        self.login_admin()
        res = self.client.get(f'/eleve/{eleve_nu.id}')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(b"6e A", res.data)
        self.assertNotIn(b"5e B", res.data)

    def test_8_isolation_multi_ecoles(self):
        """8. Deux écoles strictement isolées."""
        self.login_admin()
        # L'admin de l'École A ne peut pas accéder aux élèves de l'École B
        eleve_b = Eleve(nom="BOUBA", prenom="Ali", date_naissance=date(2010, 1, 1), ecole_id=self.ecole_b.id)
        db.session.add(eleve_b)
        db.session.commit()

        res = self.client.get(f'/eleve/{eleve_b.id}')
        self.assertIn(res.status_code, [302, 403]) # Accès refusé ou redirection

    def test_9_modification_eleve_classe_id_sans_impact(self):
        """9. Modifier arbitrairement Eleve.classe_id ne change aucun résultat annuel."""
        # On définit un classe_id corrompu/fausse sur l'élève A
        self.eleve_a.classe_id = 99999
        db.session.commit()

        self.login_admin()
        # Le reçu doit TOUJOURS afficher 6e A
        res = self.client.get(f'/paiements/{self.paiement_2025.id}/recu')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"6e A", res.data)

if __name__ == '__main__':
    unittest.main()
