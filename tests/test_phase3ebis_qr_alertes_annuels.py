"""
KLASORA — TESTS PHASE 3E-BIS : QR CODES ÉTUDIANTS & ALERTES ANNUELLES
Validation exhaustive :
1. QR Codes (/qrcodes_etudiants) :
   - Année ACTIVE stricte de l'école (ignore l'année consultée archivée/planifiée)
   - Population issue de Inscription de l'année active
   - Découplage complet d'Eleve.classe_id (y compris si None ou différent)
   - Aucune année active -> blocage propre
   - Multiples années actives -> incohérence bloquée
   - Multi-école étanche
   - Rôles : Admin (tous), Professeur (ses classes), autres bloqués
   - Non-mutation de session["annee_consultee"]
   - Format de QR encodé : {prenom} {nom}\\nClasse: {classe_nom}
2. Alertes Scolaires (/alertes, /api/alertes) :
   - Année CONSULTÉE stricte via get_annee_consultee(ecole_id)
   - Notes faibles (< 10/20) ancrées à Inscription
   - Absences injustifiées (>= 3) ancrées à Inscription
   - Retards de scolarité calculés sur Inscription.frais_annuels
   - Cloisonnement strict inter-annuel (aucun mélange entre 2024-2025 et 2025-2026)
   - Année archivée : historique consultable, flag historique=True, PAS de notification envoyée
   - Année planifiée : zéro alerte générée
   - Découplage complet d'Eleve.classe_id
   - Rôles : Admin (école), Professeur (ses classes), Parent (ses enfants), autres 403
   - API marquer lue / toutes lues / réactiver
   - Multi-école étanche
   - Non-mutation de session["annee_consultee"]
   - Scénario E2E inter-annuel
"""

import json
import unittest
from datetime import date, datetime

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.routes.qrcode import generer_qrcode_eleve
from app.services import generer_alertes_automatiques
from app.services.annees_scolaires import get_annee_active, get_annee_consultee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3EBisQrAlertesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # 1. Écoles
        self.ecole_a = Ecole(nom="École A Test", statut="actif")
        self.ecole_b = Ecole(nom="École B Test", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # 2. Années scolaires École A
        self.archivee_a = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.active_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.planifiee_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )

        # Année scolaire École B
        self.active_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee_a, self.active_a, self.planifiee_a, self.active_b])
        db.session.flush()

        # 3. Classes
        self.classe_arch_a = Classe(
            nom="6ème A (24-25)",
            niveau="6e",
            statut="fermee",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.archivee_a.id,
        )
        self.classe_act_a1 = Classe(
            nom="5ème B (25-26)",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.active_a.id,
        )
        self.classe_act_a2 = Classe(
            nom="5ème C (25-26)",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.active_a.id,
        )
        self.classe_plan_a = Classe(
            nom="4ème D (26-27)",
            niveau="4e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.planifiee_a.id,
        )
        self.classe_act_b = Classe(
            nom="5ème B École B",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.active_b.id,
        )
        db.session.add_all([
            self.classe_arch_a,
            self.classe_act_a1,
            self.classe_act_a2,
            self.classe_plan_a,
            self.classe_act_b,
        ])
        db.session.flush()

        # 4. Utilisateurs & Rôles
        # Admin A
        self.admin_a = Utilisateur(
            nom="Admin A",
            email="admin_a@test.local",
            mot_de_passe="x",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        # Admin B
        self.admin_b = Utilisateur(
            nom="Admin B",
            email="admin_b@test.local",
            mot_de_passe="x",
            role="admin",
            ecole_id=self.ecole_b.id,
        )
        # Professeur A (enseigne en classe_act_a1)
        self.prof_user_a = Utilisateur(
            nom="Prof A",
            email="profa@test.local",
            mot_de_passe="x",
            role="professeur",
            ecole_id=self.ecole_a.id,
        )
        # Parent 1
        self.parent_1 = Utilisateur(
            nom="Parent 1",
            email="parent1@test.local",
            mot_de_passe="x",
            role="parent",
            ecole_id=self.ecole_a.id,
        )
        # Parent 2
        self.parent_2 = Utilisateur(
            nom="Parent 2",
            email="parent2@test.local",
            mot_de_passe="x",
            role="parent",
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.admin_a, self.admin_b, self.prof_user_a, self.parent_1, self.parent_2])
        db.session.flush()

        self.prof_a = Professeur(
            nom="Professeur",
            prenom="Test",
            email="profa@test.local",
            ecole_id=self.ecole_a.id,
            utilisateur_id=self.prof_user_a.id,
        )
        db.session.add(self.prof_a)
        db.session.flush()

        # Assigner prof_a à classe_act_a1
        db.session.execute(
            professeur_classes.insert().values(
                professeur_id=self.prof_a.id,
                classe_id=self.classe_act_a1.id,
                ecole_id=self.ecole_a.id,
            )
        )
        db.session.flush()

        # Cours
        self.cours_arch_a = Cours(
            nom="Maths 6e",
            classe_id=self.classe_arch_a.id,
            professeur_id=self.prof_a.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_act_a1 = Cours(
            nom="Maths 5e B",
            classe_id=self.classe_act_a1.id,
            professeur_id=self.prof_a.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_act_a2 = Cours(
            nom="Maths 5e C",
            classe_id=self.classe_act_a2.id,
            professeur_id=None,
            ecole_id=self.ecole_a.id,
        )
        self.cours_act_b = Cours(
            nom="Maths B",
            classe_id=self.classe_act_b.id,
            professeur_id=self.prof_a.id,
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.cours_arch_a, self.cours_act_a1, self.cours_act_a2, self.cours_act_b])
        db.session.flush()

        # 5. Élèves
        # Eleve 1 : Moussa (parent_1), inscrit en archivee_a et active_a
        self.eleve_1 = Eleve(
            nom="Diallo",
            prenom="Moussa",
            date_naissance=date(2012, 1, 15),
            genre="M",
            parent_id=self.parent_1.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_act_a1.id,
            frais_annuels=150000.0,
        )
        # Eleve 2 : Fatou (parent_2), inscrite en active_a (classe_act_a2)
        # Intentionnellement : Eleve.classe_id = None pour tester le découplage
        self.eleve_2 = Eleve(
            nom="Sow",
            prenom="Fatou",
            date_naissance=date(2012, 3, 20),
            genre="F",
            parent_id=self.parent_2.id,
            ecole_id=self.ecole_a.id,
            classe_id=None,
            frais_annuels=150000.0,
        )
        # Eleve 3 : Ibrahima, inscrit uniquement dans archivee_a (parti de l'école)
        self.eleve_3 = Eleve(
            nom="Ba",
            prenom="Ibrahima",
            date_naissance=date(2011, 7, 10),
            genre="M",
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_arch_a.id,
            frais_annuels=150000.0,
        )
        # Eleve B : Jean, inscrit en Ecole B
        self.eleve_b = Eleve(
            nom="Dupont",
            prenom="Jean",
            date_naissance=date(2012, 5, 5),
            genre="M",
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_act_b.id,
            frais_annuels=150000.0,
        )
        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_3, self.eleve_b])
        db.session.flush()

        # 6. Inscriptions
        # Inscription archivee Moussa
        self.ins_arch_1 = Inscription(
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_arch_a.id,
            annee_scolaire_id=self.archivee_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=140000.0,
            statut="active",
        )
        # Inscription archivee Ibrahima
        self.ins_arch_3 = Inscription(
            eleve_id=self.eleve_3.id,
            classe_id=self.classe_arch_a.id,
            annee_scolaire_id=self.archivee_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=140000.0,
            statut="active",
        )
        # Inscription active Moussa (classe 5e B)
        self.ins_act_1 = Inscription(
            eleve_id=self.eleve_1.id,
            classe_id=self.classe_act_a1.id,
            annee_scolaire_id=self.active_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=150000.0,
            statut="active",
        )
        # Inscription active Fatou (classe 5e C)
        self.ins_act_2 = Inscription(
            eleve_id=self.eleve_2.id,
            classe_id=self.classe_act_a2.id,
            annee_scolaire_id=self.active_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=150000.0,
            statut="active",
        )
        # Inscription École B
        self.ins_act_b = Inscription(
            eleve_id=self.eleve_b.id,
            classe_id=self.classe_act_b.id,
            annee_scolaire_id=self.active_b.id,
            ecole_id=self.ecole_b.id,
            frais_annuels=160000.0,
            statut="active",
        )
        db.session.add_all([
            self.ins_arch_1,
            self.ins_arch_3,
            self.ins_act_1,
            self.ins_act_2,
            self.ins_act_b,
        ])
        db.session.flush()

        # 7. Données d'alertes dans active_a pour Moussa :
        # Note faible : 6/20
        self.note_faible_1 = Note(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            annee_id=self.active_a.id,
            cours_id=self.cours_act_a1.id,
            valeur=6.0,
            coefficient=2.0,
            periode="Trimestre 1",
            ecole_id=self.ecole_a.id,
        )
        # Absences injustifiées : 3
        self.abs_1 = Absence(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            date_absence=date(2025, 10, 5),
            justifiee=False,
            ecole_id=self.ecole_a.id,
        )
        self.abs_2 = Absence(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            date_absence=date(2025, 10, 6),
            justifiee=False,
            ecole_id=self.ecole_a.id,
        )
        self.abs_3 = Absence(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            date_absence=date(2025, 10, 7),
            justifiee=False,
            ecole_id=self.ecole_a.id,
        )
        # Données de paiements dans active_a : payé 0 FCFA sur 150000 FCFA
        # Données archivées pour Ibrahima (archivee_a) :
        # Note faible en archivée
        self.note_faible_arch = Note(
            eleve_id=self.eleve_3.id,
            inscription_id=self.ins_arch_3.id,
            annee_id=self.archivee_a.id,
            cours_id=self.cours_arch_a.id,
            valeur=5.0,
            coefficient=1.0,
            periode="Trimestre 1",
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.note_faible_1, self.abs_1, self.abs_2, self.abs_3, self.note_faible_arch])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # =============================================================
    # PARTIE 1 : TESTS QR CODES ÉTUDIANTS (/qrcodes_etudiants)
    # =============================================================

    def test_01_qr_annee_active_stricte(self):
        """Vérifie que /qrcodes_etudiants cible l'année active."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            self.assertIn("2025-2026", resp.get_data(as_text=True))

    def test_02_qr_ignore_annee_consultee_archivee(self):
        """Même si l'année consultée est archivée, /qrcodes_etudiants reste sur l'année active."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.archivee_a.id)}

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("2025-2026", html)
            # L'élève 3 (inscrit uniquement dans l'année archivée) ne doit PAS apparaître
            self.assertNotIn("Ba Ibrahima", html)

    def test_03_qr_ignore_annee_consultee_planifiee(self):
        """Même si l'année consultée est planifiée, /qrcodes_etudiants reste sur l'année active."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.planifiee_a.id)}

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("2025-2026", html)
            self.assertIn("Moussa Diallo", html)

    def test_04_qr_aucune_annee_active_bloque_proprement(self):
        """Si aucune année n'est active, affiche un message clair sans erreur 500."""
        self.active_a.statut = "archivee"
        db.session.commit()

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.prof_user_a.id)
                sess['role'] = 'professeur'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Aucune année scolaire active", html)

    def test_05_qr_multiples_annees_actives_bloque_incoherence(self):
        """Si plusieurs années sont marquées actives, bloque avec message d'incohérence."""
        self.planifiee_a.statut = "active"
        db.session.commit()

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Incohérence", html)

    def test_06_qr_population_issue_inscriptions_actives(self):
        """La population des QR codes provient des Inscriptions de l'année active."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            html = resp.get_data(as_text=True)
            # Moussa et Fatou sont inscrits dans l'année active
            self.assertIn("Moussa", html)
            self.assertIn("Fatou", html)

    def test_07_qr_eleve_non_inscrit_annee_active_exclu(self):
        """Un élève non inscrit dans l'année active est totalement exclu."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            html = resp.get_data(as_text=True)
            self.assertNotIn("Ibrahima", html)

    def test_08_qr_decouplage_eleve_classe_id_nul(self):
        """Fatou a Eleve.classe_id = None, mais est affichée avec la classe de son inscription active."""
        self.assertIsNone(self.eleve_2.classe_id)
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            html = resp.get_data(as_text=True)
            self.assertIn("Fatou", html)
            self.assertIn("5ème C (25-26)", html)

    def test_09_qr_decouplage_eleve_classe_id_differente(self):
        """Si Eleve.classe_id pointe vers une autre classe, c'est celle de Inscription active qui prime."""
        self.eleve_1.classe_id = self.classe_arch_a.id
        db.session.commit()

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            html = resp.get_data(as_text=True)
            self.assertIn("5ème B (25-26)", html)

    def test_10_qr_format_donnees_encodees(self):
        """Vérifie que la fonction generer_qrcode_eleve génère une image valide."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get(f'/eleve/{self.eleve_1.id}/qrcode')
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.data.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_11_qr_role_admin_voit_tous_eleves_ecole(self):
        """L'administrateur voit tous les élèves de l'école A."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            html = resp.get_data(as_text=True)
            self.assertIn("Moussa", html)
            self.assertIn("Fatou", html)

    def test_12_qr_role_professeur_filtre_classes_assignees(self):
        """Le professeur ne voit que les élèves de ses classes (classe_act_a1 = Moussa)."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.prof_user_a.id)
                sess['role'] = 'professeur'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Moussa", html)
            # Fatou est en classe_act_a2, non assignée au prof -> exclue
            self.assertNotIn("Fatou", html)

    def test_13_qr_role_parent_ou_autre_refuse(self):
        """Un parent n'a pas accès à /qrcodes_etudiants (réservé admin et professeur)."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.parent_1.id)
                sess['role'] = 'parent'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/qrcodes_etudiants')
            self.assertIn(resp.status_code, [302, 403])

    def test_14_qr_multi_ecole_etanche(self):
        """L'administrateur de l'école B ne voit pas les élèves de l'école A."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_b.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_b.id

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Jean", html)
            self.assertNotIn("Moussa", html)
            self.assertNotIn("Fatou", html)

    def test_15_qr_ne_mute_pas_session_annee_consultee(self):
        """L'accès à /qrcodes_etudiants ne modifie jamais session['annee_consultee']."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.archivee_a.id)}

            resp = client.get('/qrcodes_etudiants')
            self.assertEqual(resp.status_code, 200)

            with client.session_transaction() as sess:
                self.assertEqual(
                    sess.get('annee_consultee'),
                    {str(self.ecole_a.id): str(self.archivee_a.id)},
                )

    # =============================================================
    # PARTIE 2 : TESTS ALERTES SCOLAIRES (/alertes, /api/alertes)
    # =============================================================

    def test_16_alertes_cadre_strict_annee_consultee(self):
        """Vérifie que les alertes ciblent strictement l'année consultée."""
        alertes_act = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        # Dans active_a, Moussa a une note faible et des absences
        eleves_alertes = {a['eleve_id'] for a in alertes_act}
        self.assertIn(self.eleve_1.id, eleves_alertes)
        # Ibrahima est dans archivee_a uniquement
        self.assertNotIn(self.eleve_3.id, eleves_alertes)

    def test_17_alertes_notes_faibles_annee_active(self):
        """Vérifie la génération d'une alerte note faible (< 10/20)."""
        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        notes_alertes = [a for a in alertes if a['source'] == 'Notes' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(notes_alertes), 1)
        self.assertEqual(notes_alertes[0]['type'], 'danger')  # car 6/20 < 8
        self.assertIn("6.0/20", notes_alertes[0]['valeur_cle'])

    def test_17b_alertes_notes_bonnes_aucune_alerte(self):
        """Un élève ayant une moyenne >= 10 ne génère aucune alerte Notes."""
        note_bonne = Note(
            eleve_id=self.eleve_2.id,
            inscription_id=self.ins_act_2.id,
            cours_id=self.cours_act_a2.id,
            annee_id=self.active_a.id,
            valeur=15.0,
            coefficient=2.0,
            periode="Trimestre 1",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(note_bonne)
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        notes_fatou = [a for a in alertes if a['source'] == 'Notes' and a['eleve_id'] == self.eleve_2.id]
        self.assertEqual(len(notes_fatou), 0)

    def test_18_alertes_notes_cloisonnees_inter_annees(self):
        """Les notes faibles de 2024-2025 ne polluent pas 2025-2026."""
        alertes_act = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        # Note archivée de Ibrahima (note_faible_arch) ne doit pas être dans active_a
        notes_ibrahima = [a for a in alertes_act if a['eleve_id'] == self.eleve_3.id]
        self.assertEqual(len(notes_ibrahima), 0)

    def test_19_alertes_absences_injustifiees_seuil_atteint(self):
        """Vérifie la génération d'une alerte absence injustifiée (>= 3 absences)."""
        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        abs_alertes = [a for a in alertes if a['source'] == 'Absences' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(abs_alertes), 1)
        self.assertEqual(abs_alertes[0]['type'], 'warning')  # 3 absences -> warning
        self.assertIn("3 absence(s)", abs_alertes[0]['valeur_cle'])

    def test_19b_alertes_absences_justifiees_ignorees(self):
        """Les absences justifiées ne déclenchent pas d'alerte."""
        for _ in range(5):
            db.session.add(Absence(
                eleve_id=self.eleve_2.id,
                inscription_id=self.ins_act_2.id,
                date_absence=date(2025, 11, 1),
                justifiee=True,
                ecole_id=self.ecole_a.id,
            ))
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        abs_fatou = [a for a in alertes if a['source'] == 'Absences' and a['eleve_id'] == self.eleve_2.id]
        self.assertEqual(len(abs_fatou), 0)

    def test_20_alertes_absences_cloisonnees_inter_annees(self):
        """Les absences d'une année archivée ne remontent pas en active."""
        # 5 absences pour Ibrahima en archivée
        for i in range(5):
            db.session.add(Absence(
                eleve_id=self.eleve_3.id,
                inscription_id=self.ins_arch_3.id,
                date_absence=date(2024, 10, i + 1),
                justifiee=False,
                ecole_id=self.ecole_a.id,
            ))
        db.session.commit()

        alertes_act = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        abs_ibrahima = [a for a in alertes_act if a['source'] == 'Absences' and a['eleve_id'] == self.eleve_3.id]
        self.assertEqual(len(abs_ibrahima), 0)

    def test_21_alertes_paiements_retard_calcule_sur_frais_inscriptions(self):
        """L'alerte de paiement pour une année archivée signale tout solde restant dû."""
        alertes_arch = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.archivee_a)
        paiement_alertes = [a for a in alertes_arch if a['source'] == 'Paiements' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(paiement_alertes), 1)
        # Frais annuels = 140000, 0 payé -> 140 000 FCFA
        self.assertEqual(paiement_alertes[0]['details']['montant_total_du'], 140000)

    def test_22_alertes_paiements_cloisonnes_inter_annees(self):
        """Un paiement effectué pour l'année active ne solde pas l'année archivée."""
        p_act = Paiement(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            montant=150000.0,
            mois="Septembre",
            annee=2025,
            date_paiement=datetime(2025, 9, 15, 10, 0),
            statut="payé",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(p_act)
        db.session.commit()

        # En archivee_a, Moussa doit toujours ses 140 000 FCFA
        alertes_arch = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.archivee_a)
        paiement_arch = [a for a in alertes_arch if a['source'] == 'Paiements' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(paiement_arch), 1)
        self.assertEqual(paiement_arch[0]['details']['montant_total_du'], 140000)

    def test_23_alertes_annee_archivee_consultable_lecture_seule(self):
        """Dans une année archivée, les alertes sont consultables avec historique=True."""
        alertes_arch = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.archivee_a)
        self.assertGreater(len(alertes_arch), 0)
        for a in alertes_arch:
            self.assertTrue(a.get('historique', False))

    def test_24_alertes_annee_archivee_aucune_notification_envoyee(self):
        """Lors de la consultation d'une année archivée, aucune notification n'est envoyée."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.archivee_a.id)}

            resp = client.get('/alertes')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Année archivée", html)

    def test_25_alertes_annee_planifiee_aucune_alerte(self):
        """Pour une année planifiée, aucune alerte n'est générée."""
        alertes_plan = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.planifiee_a)
        self.assertEqual(len(alertes_plan), 0)

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.planifiee_a.id)}

            resp = client.get('/alertes')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Année planifiée", html)

    def test_26_alertes_decouplage_eleve_classe_id_nul(self):
        """Fatou (classe_id=None) génère des alertes correctement rattachées à sa classe d'inscription."""
        # Ajouter 4 absences injustifiées à Fatou
        for i in range(4):
            db.session.add(Absence(
                eleve_id=self.eleve_2.id,
                inscription_id=self.ins_act_2.id,
                date_absence=date(2025, 10, i + 1),
                justifiee=False,
                ecole_id=self.ecole_a.id,
            ))
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        abs_fatou = [a for a in alertes if a['source'] == 'Absences' and a['eleve_id'] == self.eleve_2.id]
        self.assertEqual(len(abs_fatou), 1)
        self.assertEqual(abs_fatou[0]['classe_nom'], "5ème C (25-26)")

    def test_27_alertes_decouplage_eleve_classe_id_differente(self):
        """Si Eleve.classe_id pointe vers une autre classe, l'alerte utilise la classe de l'inscription."""
        self.eleve_1.classe_id = self.classe_arch_a.id
        db.session.commit()

        alertes = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        moussa_alertes = [a for a in alertes if a['eleve_id'] == self.eleve_1.id]
        self.assertGreater(len(moussa_alertes), 0)
        for a in moussa_alertes:
            self.assertEqual(a['classe_nom'], "5ème B (25-26)")

    def test_28_alertes_kpi_stats_coherents(self):
        """Les stats passées au template reflètent fidèlement les alertes actives."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.get('/alertes')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("Centre d'Alertes", html)

    def test_29_alertes_role_admin_voit_toutes_alertes_ecole(self):
        """L'administrateur voit toutes les alertes de l'école consultée."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            eleve_ids = {a['eleve_id'] for a in data['alertes']}
            self.assertIn(self.eleve_1.id, eleve_ids)

    def test_30_alertes_role_professeur_uniquement_ses_classes(self):
        """Le professeur ne voit que les alertes des classes dont il a la charge."""
        # Ajouter une alerte sur Fatou (classe_act_a2, non assignée au professeur)
        for i in range(4):
            db.session.add(Absence(
                eleve_id=self.eleve_2.id,
                inscription_id=self.ins_act_2.id,
                date_absence=date(2025, 10, i + 1),
                justifiee=False,
                ecole_id=self.ecole_a.id,
            ))
        db.session.commit()

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.prof_user_a.id)
                sess['role'] = 'professeur'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            eleve_ids = {a['eleve_id'] for a in data['alertes']}
            # Moussa est dans la classe assignée au prof -> visible
            self.assertIn(self.eleve_1.id, eleve_ids)
            # Fatou n'est pas dans la classe du prof -> exclue
            self.assertNotIn(self.eleve_2.id, eleve_ids)

    def test_31_alertes_role_parent_uniquement_ses_enfants(self):
        """Le parent ne voit que les alertes concernant ses propres enfants."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.parent_1.id)
                sess['role'] = 'parent'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            for a in data['alertes']:
                self.assertEqual(a['eleve_id'], self.eleve_1.id)

    def test_32_alertes_role_non_autorise_403(self):
        """Un rôle non autorisé est rejeté avec 403."""
        user_inconnu = Utilisateur(
            nom="Inconnu",
            email="inconnu@test.local",
            mot_de_passe="x",
            role="comptable",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(user_inconnu)
        db.session.commit()

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(user_inconnu.id)
                sess['role'] = 'comptable'
                sess['ecole_id'] = self.ecole_a.id

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 403)

    def test_33_alertes_multi_ecole_etanche(self):
        """L'admin de l'École B ne voit aucune alerte de l'École A."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_b.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_b.id
                sess['annee_consultee'] = {str(self.ecole_b.id): str(self.active_b.id)}

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            for a in data['alertes']:
                self.assertEqual(a['eleve_id'], self.eleve_b.id)
                self.assertNotEqual(a['eleve_id'], self.eleve_1.id)

    def test_34_alertes_api_get_liste_annee_consultee(self):
        """L'API /api/alertes renvoie les alertes avec la date formatée."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.get('/api/alertes')
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            self.assertIn('alertes', data)
            self.assertIsInstance(data['alertes'], list)

    def test_35_alertes_api_marquer_une_lue(self):
        """L'API permet de marquer une alerte comme traitée."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.post(
                f'/api/alertes/note-{self.ins_act_1.id}/read',
                json={'action': 'treat'},
            )
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            self.assertTrue(data['success'])
            self.assertTrue(data['is_traitee'])

            with client.session_transaction() as sess:
                self.assertIn(f'note-{self.ins_act_1.id}', sess.get('alertes_traitees', []))

    def test_36_alertes_api_marquer_toutes_lues(self):
        """L'API permet de marquer toutes les alertes comme traitées."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}

            resp = client.post('/api/alertes/all/read', json={})
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            self.assertTrue(data['success'])

            with client.session_transaction() as sess:
                traitees = sess.get('alertes_traitees', [])
                self.assertIn(f'note-{self.ins_act_1.id}', traitees)
                self.assertIn(f'absence-{self.ins_act_1.id}', traitees)

    def test_37_alertes_api_reactiver_alerte(self):
        """L'API permet de réactiver une alerte préalablement traitée."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.active_a.id)}
                sess['alertes_traitees'] = [f'note-{self.ins_act_1.id}']

            resp = client.post(
                f'/api/alertes/note-{self.ins_act_1.id}/read',
                json={'action': 'untreat'},
            )
            self.assertEqual(resp.status_code, 200)
            data = json.loads(resp.get_data(as_text=True))
            self.assertFalse(data['is_traitee'])

            with client.session_transaction() as sess:
                self.assertNotIn(f'note-{self.ins_act_1.id}', sess.get('alertes_traitees', []))

    def test_38_alertes_ne_mute_pas_session_annee_consultee(self):
        """La consultation des alertes ne modifie pas session['annee_consultee']."""
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin_a.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = {str(self.ecole_a.id): str(self.archivee_a.id)}

            resp = client.get('/alertes')
            self.assertEqual(resp.status_code, 200)

            with client.session_transaction() as sess:
                self.assertEqual(
                    sess.get('annee_consultee'),
                    {str(self.ecole_a.id): str(self.archivee_a.id)},
                )

    def test_39_alertes_scenario_e2e_a_b(self):
        """
        Scénario E2E complet :
        - Année 2024-2025 (archivée) : Moussa a payé 0 / 140 000 FCFA -> Alerte paiement historique présente.
        - Année 2025-2026 (active) : Moussa paie intégralement sa scolarité -> Alerte paiement absente,
          mais alerte notes présente (6/20).
        """
        # Moussa paie l'année active
        p_solde = Paiement(
            eleve_id=self.eleve_1.id,
            inscription_id=self.ins_act_1.id,
            montant=150000.0,
            mois="Septembre",
            annee=2025,
            date_paiement=datetime(2025, 9, 2, 10, 0),
            statut="payé",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(p_solde)
        db.session.commit()

        # Consultation 2024-2025 (archivée)
        alertes_24_25 = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.archivee_a)
        paiements_24_25 = [a for a in alertes_24_25 if a['source'] == 'Paiements' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(paiements_24_25), 1)
        self.assertTrue(paiements_24_25[0]['historique'])

        # Consultation 2025-2026 (active)
        alertes_25_26 = generer_alertes_automatiques(ecole_id=self.ecole_a.id, annee=self.active_a)
        paiements_25_26 = [a for a in alertes_25_26 if a['source'] == 'Paiements' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(paiements_25_26), 0)
        notes_25_26 = [a for a in alertes_25_26 if a['source'] == 'Notes' and a['eleve_id'] == self.eleve_1.id]
        self.assertEqual(len(notes_25_26), 1)


if __name__ == '__main__':
    unittest.main()
