"""
Tests KLASORA — Phase 5O : Absences Uniquement (Absence seule source d'assiduité).
MAXIMUM 6 tests ciblés.
"""
from datetime import date
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Absence,
    Professeur,
    Utilisateur,
    professeur_classes,
)
from app.services.absences_annuelles import (
    verifier_mutation_absence,
    get_absences_annee,
)
from app.services.semestres import (
    configurer_semestres_annee,
    compter_absences_semestre,
    compter_retards_semestre,
)
from app.services.bulletins_annuels import calculer_bulletin_data

from sqlalchemy.pool import StaticPool


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-phase5o"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPhase5OAbsencesOnly(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # École
        self.ecole = Ecole(
            nom="École Pilote Niamey",
            adresse="Quartier Plateau",
            telephone="90000000",
            email="contact@pilote.ne",
            statut="actif"
        )
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire active (2025-2026)
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        # Année scolaire archivée (2024-2025)
        self.annee_archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 15),
            date_fin=date(2025, 6, 30),
            statut="archivee",
            ecole_id=self.ecole.id
        )
        db.session.add_all([self.annee_active, self.annee_archivee])
        db.session.commit()

        # Semestres 2025-2026 (S1: 15/09/2025 -> 31/01/2026, S2: 01/02/2026 -> 30/06/2026)
        configurer_semestres_annee(self.ecole.id, self.annee_active.id, date(2026, 1, 31))

        # Niveau & Classe
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.commit()

        self.classe_a = Classe(
            nom="6ème A", niveau_id=self.niveau.id, ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        self.classe_b = Classe(
            nom="6ème B", niveau_id=self.niveau.id, ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id, statut="ouverte"
        )
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.commit()

        # Utilisateurs & Professeurs
        self.admin = Utilisateur(nom="Admin", prenom="User", email="admin@test.ne", mot_de_passe="pass123", role="admin", ecole_id=self.ecole.id)
        self.user_prof_a = Utilisateur(nom="ProfA", prenom="User", email="profa@test.ne", mot_de_passe="pass123", role="professeur", ecole_id=self.ecole.id)
        db.session.add_all([self.admin, self.user_prof_a])
        db.session.commit()

        self.prof_a = Professeur(nom="ProfA", prenom="User", code_prof="P001", utilisateur_id=self.user_prof_a.id, ecole_id=self.ecole.id)
        db.session.add(self.prof_a)
        db.session.commit()

        db.session.execute(professeur_classes.insert().values(professeur_id=self.prof_a.id, classe_id=self.classe_a.id, ecole_id=self.ecole.id))
        db.session.commit()

        # Élève & Inscription active (Classe A)
        self.eleve = Eleve(nom="Diallo", prenom="Moussa", date_naissance=date(2013, 1, 1), ecole_id=self.ecole.id, classe_id=self.classe_a.id)
        db.session.add(self.eleve)
        db.session.commit()

        self.ins_active = Inscription(
            eleve_id=self.eleve.id, classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_active.id, ecole_id=self.ecole.id,
            frais_annuels=150000.0, statut="inscrit"
        )
        db.session.add(self.ins_active)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # Test 1 : Absence S1 comptée uniquement dans S1
    def test_01_absence_s1_comptee_uniquement_s1(self):
        abs_s1 = Absence(
            date_absence=date(2025, 10, 20),
            motif="Maladie",
            justifiee=True,
            eleve_id=self.eleve.id,
            ecole_id=self.ecole.id,
            inscription_id=self.ins_active.id
        )
        db.session.add(abs_s1)
        db.session.commit()

        cnt_s1 = compter_absences_semestre(self.ecole.id, self.annee_active.id, self.ins_active.id, "Semestre 1")
        cnt_s2 = compter_absences_semestre(self.ecole.id, self.annee_active.id, self.ins_active.id, "Semestre 2")

        self.assertEqual(cnt_s1, 1)
        self.assertEqual(cnt_s2, 0)

    # Test 2 : Absence S2 comptée uniquement dans S2
    def test_02_absence_s2_comptee_uniquement_s2(self):
        abs_s2 = Absence(
            date_absence=date(2026, 3, 15),
            motif="Voyage",
            justifiee=False,
            eleve_id=self.eleve.id,
            ecole_id=self.ecole.id,
            inscription_id=self.ins_active.id
        )
        db.session.add(abs_s2)
        db.session.commit()

        cnt_s1 = compter_absences_semestre(self.ecole.id, self.annee_active.id, self.ins_active.id, "Semestre 1")
        cnt_s2 = compter_absences_semestre(self.ecole.id, self.annee_active.id, self.ins_active.id, "Semestre 2")

        self.assertEqual(cnt_s1, 0)
        self.assertEqual(cnt_s2, 1)

    # Test 3 : Absence d'une autre année exclue des consultations de l'année active
    def test_03_absence_autre_annee_exclue(self):
        ins_arch = Inscription(
            eleve_id=self.eleve.id, classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_archivee.id, ecole_id=self.ecole.id,
            frais_annuels=150000.0, statut="inscrit"
        )
        db.session.add(ins_arch)
        db.session.commit()

        abs_old = Absence(
            date_absence=date(2024, 11, 10),
            motif="Ancienne absence",
            justifiee=True,
            eleve_id=self.eleve.id,
            ecole_id=self.ecole.id,
            inscription_id=ins_arch.id
        )
        db.session.add(abs_old)
        db.session.commit()

        absences_annee_active = get_absences_annee(self.ecole.id, self.annee_active, self.admin)
        self.assertEqual(len(absences_annee_active), 0)

    # Test 4 : Professeur hors périmètre -> Création absence refusée
    def test_04_professeur_hors_perimetre_refuse(self):
        eleve_b = Eleve(nom="Sani", prenom="Ibrahim", date_naissance=date(2013, 2, 2), ecole_id=self.ecole.id, classe_id=self.classe_b.id)
        db.session.add(eleve_b)
        db.session.commit()

        ins_b = Inscription(
            eleve_id=eleve_b.id, classe_id=self.classe_b.id,
            annee_scolaire_id=self.annee_active.id, ecole_id=self.ecole.id,
            frais_annuels=150000.0, statut="inscrit"
        )
        db.session.add(ins_b)
        db.session.commit()

        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole.id, self.annee_active, self.user_prof_a,
            eleve_b.id, None, date(2025, 11, 5)
        )
        self.assertIsNotNone(error)

    # Test 5 : Année archivée -> Mutation absence refusée
    def test_05_annee_archivee_mutation_refusee(self):
        _e, _c, _i, error = verifier_mutation_absence(
            self.ecole.id, self.annee_archivee, self.admin,
            self.eleve.id, None, date(2024, 10, 10)
        )
        self.assertIsNotNone(error)
        self.assertIn("archivée", error.lower())

    # Test 6 : Bulletin/Rapport -> Nombre d'absences correct, retards neutralisés (0)
    def test_06_bulletin_absences_et_retards_neutralises(self):
        abs_s1 = Absence(
            date_absence=date(2025, 11, 12),
            motif="Raison familiale",
            justifiee=False,
            eleve_id=self.eleve.id,
            ecole_id=self.ecole.id,
            inscription_id=self.ins_active.id
        )
        db.session.add(abs_s1)
        db.session.commit()

        b_data, err = calculer_bulletin_data(self.ecole.id, self.annee_active, self.ins_active, "Semestre 1")
        self.assertIsNone(err)
        self.assertEqual(b_data['nb_absences'], 1)
        self.assertEqual(b_data['nb_retards'], 0)
        self.assertEqual(compter_retards_semestre(self.ecole.id, self.annee_active.id, self.ins_active.id, "Semestre 1"), 0)


if __name__ == '__main__':
    unittest.main()

