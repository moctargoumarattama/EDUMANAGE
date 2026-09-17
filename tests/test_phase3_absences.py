"""
Tests Phase 3 : Optimisation des performances et isolation stricte du module Absences.
- Professeur ne voit pas les classes d'un autre professeur
- Professeur ne voit pas les absences d'une autre école
- Admin reste limité à son école
- Année scolaire respectée (Inscription comme vérité)
- Aucun N+1 sur Classe.niveau_scolaire
- Aucun N+1 sur Absence.eleve et Absence.cours
- Déduplication de _professeur_classe_ids dans le scope requête
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    Absence,
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.services.absences_annuelles import (
    _professeur_classe_ids,
    get_absences_annee,
    get_classes_absences,
    get_inscriptions_absences,
)
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class Phase3AbsencesTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-phase3-absences"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class Phase3AbsencesTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(Phase3AbsencesTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Écoles A et B
        self.ecole_a = Ecole(nom="École A", adresse="Niamey", telephone="11111111", email="contact@ecole-a.ne", statut="actif")
        self.ecole_b = Ecole(nom="École B", adresse="Maradi", telephone="22222222", email="contact@ecole-b.ne", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # Années scolaires
        self.annee_a_2025 = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30), statut="active", ecole_id=self.ecole_a.id)
        self.annee_a_2024 = AnneeScolaire(nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 6, 30), statut="archivee", ecole_id=self.ecole_a.id)
        self.annee_b_2025 = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.annee_a_2025, self.annee_a_2024, self.annee_b_2025])
        db.session.commit()

        # Niveaux scolaires
        self.niv_6e = NiveauScolaire(code="6e", nom="Sixième", cycle="college", ordre=1)
        self.niv_5e = NiveauScolaire(code="5e", nom="Cinquième", cycle="college", ordre=2)
        db.session.add_all([self.niv_6e, self.niv_5e])
        db.session.commit()

        self.cfg_6e = AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, niveau_id=self.niv_6e.id, actif=True)
        self.cfg_5e = AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, niveau_id=self.niv_5e.id, actif=True)
        db.session.add_all([self.cfg_6e, self.cfg_5e])
        db.session.commit()

        # Classes
        self.classe_a1 = Classe(nom="6e A", niveau="6e", niveau_id=self.niv_6e.id, ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, capacite=30)
        self.classe_a2 = Classe(nom="5e A", niveau="5e", niveau_id=self.niv_5e.id, ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a_2025.id, capacite=30)
        self.classe_b1 = Classe(nom="6e B-EcoleB", niveau="6e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b_2025.id, capacite=30)
        db.session.add_all([self.classe_a1, self.classe_a2, self.classe_b1])
        db.session.commit()

        # Utilisateurs
        self.admin_a = Utilisateur(nom="Admin", prenom="A", email="admin@ecole-a.local", mot_de_passe=generate_password_hash("Secret123!"), role="admin", ecole_id=self.ecole_a.id)
        self.u_prof1 = Utilisateur(nom="Prof1", prenom="A", email="prof1@ecole-a.local", mot_de_passe=generate_password_hash("Secret123!"), role="professeur", ecole_id=self.ecole_a.id)
        self.u_prof2 = Utilisateur(nom="Prof2", prenom="A", email="prof2@ecole-a.local", mot_de_passe=generate_password_hash("Secret123!"), role="professeur", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin_a, self.u_prof1, self.u_prof2])
        db.session.commit()

        self.prof1 = Professeur(nom="KOUAME", prenom="Prof1", utilisateur_id=self.u_prof1.id, ecole_id=self.ecole_a.id, date_naissance=date(1980, 1, 1), specialite="Maths")
        self.prof2 = Professeur(nom="DIALLO", prenom="Prof2", utilisateur_id=self.u_prof2.id, ecole_id=self.ecole_a.id, date_naissance=date(1985, 1, 1), specialite="Histoire")
        db.session.add_all([self.prof1, self.prof2])
        db.session.commit()

        # Assigner prof1 à classe_a1 via Cours
        self.cours_prof1 = Cours(nom="Mathématiques 6e", ecole_id=self.ecole_a.id, classe_id=self.classe_a1.id, professeur_id=self.prof1.id)
        self.cours_prof2 = Cours(nom="Histoire 5e", ecole_id=self.ecole_a.id, classe_id=self.classe_a2.id, professeur_id=self.prof2.id)
        db.session.add_all([self.cours_prof1, self.cours_prof2])
        db.session.commit()

        # Élèves et inscriptions
        self.eleve1 = Eleve(nom="KONE", prenom="Moussa", date_naissance=date(2013, 5, 10), ecole_id=self.ecole_a.id)
        self.eleve2 = Eleve(nom="TRAORE", prenom="Fatou", date_naissance=date(2012, 8, 15), ecole_id=self.ecole_a.id)
        self.eleve_b = Eleve(nom="SANI", prenom="Idrissa", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_b.id)
        db.session.add_all([self.eleve1, self.eleve2, self.eleve_b])
        db.session.commit()

        self.insc1 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve1.id, classe_id=self.classe_a1.id, annee_scolaire_id=self.annee_a_2025.id, statut="inscrit")
        self.insc2 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve2.id, classe_id=self.classe_a2.id, annee_scolaire_id=self.annee_a_2025.id, statut="inscrit")
        self.insc_b = Inscription(ecole_id=self.ecole_b.id, eleve_id=self.eleve_b.id, classe_id=self.classe_b1.id, annee_scolaire_id=self.annee_b_2025.id, statut="inscrit")
        db.session.add_all([self.insc1, self.insc2, self.insc_b])
        db.session.commit()

        # Absences
        self.abs1 = Absence(ecole_id=self.ecole_a.id, eleve_id=self.eleve1.id, cours_id=self.cours_prof1.id, inscription_id=self.insc1.id, date_absence=date(2025, 10, 5), motif="Maladie", justifiee=True)
        self.abs2 = Absence(ecole_id=self.ecole_a.id, eleve_id=self.eleve2.id, cours_id=self.cours_prof2.id, inscription_id=self.insc2.id, date_absence=date(2025, 10, 6), motif="Retard bus", justifiee=False)
        self.abs_b = Absence(ecole_id=self.ecole_b.id, eleve_id=self.eleve_b.id, cours_id=None, inscription_id=self.insc_b.id, date_absence=date(2025, 10, 5), motif="Absence Ecole B", justifiee=False)
        db.session.add_all([self.abs1, self.abs2, self.abs_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_01_professeur_ne_voit_pas_classes_autre_professeur(self):
        """1. Professeur ne voit QUE ses classes assignées (via ses cours) dans get_classes_absences."""
        classes_prof1 = get_classes_absences(self.ecole_a.id, self.annee_a_2025, self.u_prof1)
        classes_prof2 = get_classes_absences(self.ecole_a.id, self.annee_a_2025, self.u_prof2)

        self.assertEqual(len(classes_prof1), 1)
        self.assertEqual(classes_prof1[0].id, self.classe_a1.id)

        self.assertEqual(len(classes_prof2), 1)
        self.assertEqual(classes_prof2[0].id, self.classe_a2.id)

    def test_02_professeur_ne_voit_pas_absences_autre_ecole_ni_autre_classe(self):
        """2. Professeur ne voit que les absences de ses propres cours/classes et aucune absence d'autre école."""
        absences_prof1 = get_absences_annee(self.ecole_a.id, self.annee_a_2025, self.u_prof1)
        ids = [a.id for a in absences_prof1]

        self.assertIn(self.abs1.id, ids)
        self.assertNotIn(self.abs2.id, ids, "Prof1 ne doit pas voir l'absence du cours de Prof2")
        self.assertNotIn(self.abs_b.id, ids, "Prof1 ne doit pas voir les absences d'une autre école")

    def test_03_admin_reste_limite_a_son_ecole(self):
        """3. L'Admin ne voit que les absences de son école et toutes les classes de son école."""
        absences_admin = get_absences_annee(self.ecole_a.id, self.annee_a_2025, self.admin_a)
        ids = [a.id for a in absences_admin]

        self.assertIn(self.abs1.id, ids)
        self.assertIn(self.abs2.id, ids)
        self.assertNotIn(self.abs_b.id, ids, "L'Admin d'école A ne doit voir aucune absence d'école B")

        classes_admin = get_classes_absences(self.ecole_a.id, self.annee_a_2025, self.admin_a)
        classes_admin_ids = [c.id for c in classes_admin]
        self.assertIn(self.classe_a1.id, classes_admin_ids)
        self.assertIn(self.classe_a2.id, classes_admin_ids)
        self.assertNotIn(self.classe_b1.id, classes_admin_ids)

    def test_04_annee_scolaire_respectee_via_inscription(self):
        """4. Les absences d'une autre année scolaire n'apparaissent pas dans l'année consultée."""
        # Créer une absence sur l'année archivée 2024
        insc_2024 = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve1.id, classe_id=self.classe_a1.id, annee_scolaire_id=self.annee_a_2024.id, statut="inscrit")
        db.session.add(insc_2024)
        db.session.flush()
        abs_2024 = Absence(ecole_id=self.ecole_a.id, eleve_id=self.eleve1.id, inscription_id=insc_2024.id, date_absence=date(2024, 11, 1), motif="Ancien")
        db.session.add(abs_2024)
        db.session.commit()

        absences_2025 = get_absences_annee(self.ecole_a.id, self.annee_a_2025, self.admin_a)
        ids_2025 = [a.id for a in absences_2025]
        self.assertNotIn(abs_2024.id, ids_2025, "L'absence 2024 ne doit pas apparaître dans la consultation 2025")

        absences_2024 = get_absences_annee(self.ecole_a.id, self.annee_a_2024, self.admin_a)
        ids_2024 = [a.id for a in absences_2024]
        self.assertIn(abs_2024.id, ids_2024)

    def test_05_aucun_n_plus_un_sur_niveau_scolaire(self):
        """5. get_classes_absences charge niveau_scolaire via joinedload (0 requête supplémentaire lors de l'accès)."""
        classes = get_classes_absences(self.ecole_a.id, self.annee_a_2025, self.admin_a)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        # Accès à niveau_scolaire sur toutes les classes
        noms_niveaux = [c.niveau_scolaire.nom if c.niveau_scolaire else "" for c in classes]

        self.assertEqual(len(queries), 0, "L'accès à niveau_scolaire ne doit émettre aucun SELECT grâce au joinedload")
        self.assertIn("Sixième", noms_niveaux)
        self.assertIn("Cinquième", noms_niveaux)

    def test_06_aucun_n_plus_un_sur_absence_eleve_et_cours(self):
        """6. get_absences_annee charge eleve et cours.classe via joinedload (0 requête supplémentaire)."""
        absences = get_absences_annee(self.ecole_a.id, self.annee_a_2025, self.admin_a)

        queries = []
        @event.listens_for(Engine, 'before_cursor_execute')
        def bce(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        # Accès aux relations eleve, cours et cours.classe
        for a in absences:
            _ = (a.eleve.nom if a.eleve else "")
            _ = (a.cours.nom if a.cours else "")
            if a.cours:
                _ = (a.cours.classe.nom if a.cours.classe else "")

        self.assertEqual(len(queries), 0, "L'accès à absence.eleve, absence.cours et cours.classe ne doit émettre aucun SELECT")

    def test_07_deduplication_professeur_classe_ids_dans_requete(self):
        """7. _professeur_classe_ids est mémoïsé dans flask.g au cours d'un contexte de requête HTTP."""
        with self.app.test_request_context('/absences'):
            annee_id = self.annee_a_2025.id
            _ = (self.u_prof1.id, self.u_prof1.professeur_rel.id)

            queries = []
            @event.listens_for(Engine, 'before_cursor_execute')
            def bce(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)

            ids1 = _professeur_classe_ids(self.u_prof1, annee_id)
            ids2 = _professeur_classe_ids(self.u_prof1, annee_id)
            ids3 = _professeur_classe_ids(self.u_prof1, annee_id)

            self.assertEqual(ids1, ids2)
            self.assertEqual(ids2, ids3)
            # Une seule requête émise pour les 3 appels
            self.assertEqual(len(queries), 1, "_professeur_classe_ids ne doit s'exécuter qu'une seule fois par requête HTTP")


if __name__ == '__main__':
    unittest.main()
