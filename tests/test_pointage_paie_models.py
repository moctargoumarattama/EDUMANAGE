import unittest
from datetime import date
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Professeur, PointagePersonnel, FichePaiePersonnel


class PointagePaieTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-pointage-paie"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestPointagePaieModels(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PointagePaieTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Établissement
        self.ecole = Ecole(nom="Groupe Scolaire Excellence")
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # Utilisateur Admin (qui pointe le personnel)
        self.user_admin = Utilisateur(
            nom="ADMIN",
            prenom="Directeur",
            email="directeur@excellence.edu",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        db.session.add(self.user_admin)
        db.session.commit()

        # Utilisateur Professeur 1 (Fixe)
        self.user_prof1 = Utilisateur(
            nom="BAH",
            prenom="Amadou",
            email="amadou.bah@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof1)
        db.session.commit()

        self.prof_fixe = Professeur(
            nom="BAH",
            prenom="Amadou",
            email="amadou.bah@excellence.edu",
            utilisateur_id=self.user_prof1.id,
            ecole_id=self.ecole.id,
            salaire_base=300000.0,
            taux_horaire=0.0
        )
        db.session.add(self.prof_fixe)

        # Utilisateur Professeur 2 (Horaire / Vacataire)
        self.user_prof2 = Utilisateur(
            nom="TRAORE",
            prenom="Fatou",
            email="fatou.traore@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof2)
        db.session.commit()

        self.prof_horaire = Professeur(
            nom="TRAORE",
            prenom="Fatou",
            email="fatou.traore@excellence.edu",
            utilisateur_id=self.user_prof2.id,
            ecole_id=self.ecole.id,
            salaire_base=0.0,
            taux_horaire=5000.0
        )
        db.session.add(self.prof_horaire)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_professeur_remuneration_fields(self):
        """Vérifie que les informations salariales sont bien associées au professeur."""
        self.assertEqual(self.prof_fixe.salaire_base, 300000.0)
        self.assertEqual(self.prof_fixe.taux_horaire, 0.0)

        data = self.prof_fixe.to_dict()
        self.assertEqual(data["salaire_base"], 300000.0)

        self.assertEqual(self.prof_horaire.taux_horaire, 5000.0)
        data_h = self.prof_horaire.to_dict()
        self.assertEqual(data_h["taux_horaire"], 5000.0)

    def test_pointage_personnel_creation_and_statuses(self):
        """Vérifie les pointages enregistrés par l'administration (sans jour de congé)."""
        # 1. Pointage Présent
        p1 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 1),
            statut="present",
            heures_prevues=4.0,
            heures_effectuees=4.0,
            creneau="08:00 - 12:00",
            heure_arrivee="07:55",
            heure_depart="12:05",
            pointe_par_id=self.user_admin.id
        )
        db.session.add(p1)

        # 2. Pointage En retard
        p2 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 2),
            statut="retard",
            retard_minutes=25,
            heures_prevues=4.0,
            heures_effectuees=3.5,
            creneau="08:00 - 12:00",
            heure_arrivee="08:25",
            motif="Embouteillage",
            pointe_par_id=self.user_admin.id
        )
        db.session.add(p2)

        # 3. Pointage Absent justifié
        p3 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 3),
            statut="absent_justifie",
            heures_prevues=4.0,
            heures_effectuees=0.0,
            motif="Certificat médical fourni",
            pointe_par_id=self.user_admin.id
        )
        db.session.add(p3)

        # 4. Pointage Absent injustifié
        p4 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 4),
            statut="absent_injustifie",
            heures_prevues=4.0,
            heures_effectuees=0.0,
            motif="Non prévenu",
            pointe_par_id=self.user_admin.id
        )
        db.session.add(p4)
        db.session.commit()

        # Vérification via la relation professeur
        pointages = self.prof_fixe.pointages
        self.assertEqual(len(pointages), 4)

        statuts = [p.statut for p in pointages]
        self.assertIn("present", statuts)
        self.assertIn("retard", statuts)
        self.assertIn("absent_justifie", statuts)
        self.assertIn("absent_injustifie", statuts)
        # RÈGLE ABSOLUE : PAS DE JOUR DE CONGÉ
        self.assertNotIn("conge", statuts)

        # Vérification sérialisation to_dict
        d = p2.to_dict()
        self.assertEqual(d["statut"], "retard")
        self.assertEqual(d["retard_minutes"], 25)
        self.assertEqual(d["pointe_par_nom"], "Directeur ADMIN")

    def test_pointage_unique_prof_date_constraint(self):
        """Vérifie l'unicité du pointage par professeur et par date."""
        p1 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            date_pointage=date(2025, 10, 5),
            statut="present"
        )
        db.session.add(p1)
        db.session.commit()

        # Deuxième pointage pour le même prof et la même date => Doit échouer
        p2 = PointagePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            date_pointage=date(2025, 10, 5),
            statut="retard"
        )
        db.session.add(p2)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_fiche_paie_calcul_salaire_fixe(self):
        """Vérifie le calcul automatique pour un professeur à salaire fixe mensuel."""
        fiche = FichePaiePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            salaire_base=300000.0,
            taux_horaire=0.0,
            heures_prevues=60.0,
            heures_travaillees=58.0,
            jours_presence=20,
            retards_total_minutes=45,
            absences_injustifiees=0,
            absences_justifiees=1,
            primes=25000.0,
            deductions=10000.0,
            cree_par_id=self.user_admin.id
        )
        fiche.recalculer_montants()
        db.session.add(fiche)
        db.session.commit()

        self.assertEqual(fiche.salaire_brut, 300000.0)
        # salaire_net = 300 000 + 25 000 - 10 000 = 315 000
        self.assertEqual(fiche.salaire_net, 315000.0)

        # Simuler un paiement
        fiche.montant_paye = 315000.0
        fiche.statut_paiement = "paye"
        fiche.date_paiement = date(2025, 10, 31)
        fiche.mode_paiement = "virement"
        fiche.reference_paiement = "VIR-2025-10-001"
        db.session.commit()

        d = fiche.to_dict()
        self.assertEqual(d["statut_paiement"], "paye")
        self.assertEqual(d["solde_restant"], 0.0)
        self.assertEqual(d["montant_paye"], 315000.0)

    def test_fiche_paie_calcul_salaire_horaire_et_solde(self):
        """Vérifie le calcul automatique pour un professeur payé au taux horaire (vacataire)."""
        fiche = FichePaiePersonnel(
            professeur_id=self.prof_horaire.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            salaire_base=0.0,
            taux_horaire=5000.0,
            heures_prevues=30.0,
            heures_travaillees=32.0,  # 32 heures effectuées
            jours_presence=12,
            retards_total_minutes=0,
            absences_injustifiees=0,
            absences_justifiees=0,
            primes=10000.0,
            deductions=0.0,
            cree_par_id=self.user_admin.id
        )
        fiche.recalculer_montants()
        db.session.add(fiche)
        db.session.commit()

        # 32 h * 5000 = 160 000 salaire brut
        self.assertEqual(fiche.salaire_brut, 160000.0)
        # salaire_net = 160 000 + 10 000 = 170 000
        self.assertEqual(fiche.salaire_net, 170000.0)

        # Paiement partiel (acompte)
        fiche.montant_paye = 100000.0
        fiche.statut_paiement = "partiel"
        fiche.mode_paiement = "especes"
        db.session.commit()

        d = fiche.to_dict()
        self.assertEqual(d["statut_paiement"], "partiel")
        # solde restant = 170 000 - 100 000 = 70 000
        self.assertEqual(d["solde_restant"], 70000.0)

    def test_fiche_paie_unique_periode_constraint(self):
        """Vérifie qu'il ne peut y avoir qu'une seule fiche de paie par prof et par mois."""
        f1 = FichePaiePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            salaire_base=300000.0,
            salaire_brut=300000.0,
            salaire_net=300000.0
        )
        db.session.add(f1)
        db.session.commit()

        # Deuxième fiche pour le même prof et même mois/année => Doit échouer
        f2 = FichePaiePersonnel(
            professeur_id=self.prof_fixe.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025 (Doublon)",
            salaire_base=300000.0,
            salaire_brut=300000.0,
            salaire_net=300000.0
        )
        db.session.add(f2)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


if __name__ == '__main__':
    unittest.main()
