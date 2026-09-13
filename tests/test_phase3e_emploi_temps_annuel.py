"""
KLASORA — TESTS PHASE 3E : ANNUALISATION DE L'EMPLOI DU TEMPS
Validation exhaustive :
1. Rattachement strict Creneau -> Classe -> AnneeScolaire
2. Cycle de vie :
   - Active : création, modification, suppression autorisées
   - Planifiée : création, modification, suppression autorisées (préparation de rentrée)
   - Archivée : lecture seule stricte (interdiction de création, modification, suppression)
3. Conflits horaires dans la même année :
   - Même classe + chevauchement détecté
   - Même professeur + chevauchement détecté
   - Même salle + chevauchement détecté
   - Créneaux adjacents (08:00-09:00 et 09:00-10:00) autorisés
   - Conflits d'autres années ignorés
4. Rôles & Sécurité : Admin, Professeur, Parent (propre enfant via Inscription), Multi-écoles
5. Découplage complet d'avec Eleve.classe_id (y compris si None ou classe différente)
6. Règle 2C-5D : non-mutation de session["annee_consultee"]
7. Activation d'année : conservation des créneaux préparés sans copie ni duplication
8. Scénario End-to-End A/B complet
9. Vérification sur la vraie base SQLite migrée instance/ecole.db
"""

import os
import sqlite3
import unittest
from datetime import date, datetime, time

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    Inscription,
    Professeur,
    Utilisateur,
)
from app.services.annees_scolaires import get_annee_consultee
from app.services.emploi_temps_annuel import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    detecter_conflits,
    donnees_impression_classe,
    get_classes_pour_utilisateur,
    get_creneaux_annee,
    peut_modifier_emploi_temps,
    statut_annee_emploi,
    supprimer_creneau,
    valider_et_creer_creneau,
    valider_et_modifier_creneau,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3EEmploiTempsAnnuelTestCase(unittest.TestCase):
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
        self.archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )

        # Année scolaire École B
        self.annee_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        # 3. Classes
        self.classe_arch = Classe(
            nom="6ème A (24-25)",
            niveau="6e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.archivee.id,
        )
        self.classe_act = Classe(
            nom="5ème B (25-26)",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.active.id,
        )
        self.classe_act_2 = Classe(
            nom="5ème C (25-26)",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.active.id,
        )
        self.classe_plan = Classe(
            nom="4ème D (26-27)",
            niveau="4e",
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.planifiee.id,
        )
        self.classe_b = Classe(
            nom="Classe B",
            niveau="5e",
            statut="ouverte",
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id,
        )
        db.session.add_all([
            self.classe_arch,
            self.classe_act,
            self.classe_act_2,
            self.classe_plan,
            self.classe_b,
        ])
        db.session.flush()

        # 4. Professeurs
        self.prof_user_1 = Utilisateur(
            nom="Prof Un", email="profun@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.prof_user_2 = Utilisateur(
            nom="Prof Deux", email="profdeux@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.prof_user_b = Utilisateur(
            nom="Prof B", email="profb@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.prof_user_1, self.prof_user_2, self.prof_user_b])
        db.session.flush()

        self.prof_1 = Professeur(
            nom="Un", prenom="Prof", email="profun@test.local", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_user_1.id
        )
        self.prof_2 = Professeur(
            nom="Deux", prenom="Prof", email="profdeux@test.local", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_user_2.id
        )
        self.prof_b = Professeur(
            nom="B", prenom="Prof", email="profb@test.local", ecole_id=self.ecole_b.id, utilisateur_id=self.prof_user_b.id
        )
        db.session.add_all([self.prof_1, self.prof_2, self.prof_b])
        db.session.flush()

        # 5. Cours
        self.cours_act_1 = Cours(
            nom="Mathématiques Actives",
            classe_id=self.classe_act.id,
            professeur_id=self.prof_1.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_act_2 = Cours(
            nom="Français Actif",
            classe_id=self.classe_act.id,
            professeur_id=self.prof_2.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_act_2nd = Cours(
            nom="Histoire Active",
            classe_id=self.classe_act_2.id,
            professeur_id=self.prof_1.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_arch = Cours(
            nom="Mathématiques Archivées",
            classe_id=self.classe_arch.id,
            professeur_id=self.prof_1.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_plan = Cours(
            nom="Mathématiques Planifiées",
            classe_id=self.classe_plan.id,
            professeur_id=self.prof_1.id,
            ecole_id=self.ecole_a.id,
        )
        self.cours_b = Cours(
            nom="Cours B",
            classe_id=self.classe_b.id,
            professeur_id=self.prof_b.id,
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([
            self.cours_act_1,
            self.cours_act_2,
            self.cours_act_2nd,
            self.cours_arch,
            self.cours_plan,
            self.cours_b,
        ])
        db.session.flush()

        # 6. Utilisateurs Admin & Parents
        self.admin = Utilisateur(
            nom="Admin A", email="admin3e@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id
        )
        self.parent = Utilisateur(
            nom="Parent Un", email="parentun3e@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id
        )
        self.parent_autre = Utilisateur(
            nom="Parent Autre", email="parentautre3e@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id
        )
        db.session.add_all([self.admin, self.parent, self.parent_autre])
        db.session.flush()

        # 7. Élèves et Inscriptions
        self.eleve = Eleve(
            nom="Diallo",
            prenom="Moussa",
            date_naissance=date(2012, 5, 14),
            genre="M",
            parent_id=self.parent.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_act.id,
        )
        self.eleve_autre = Eleve(
            nom="Sow",
            prenom="Fatou",
            date_naissance=date(2012, 8, 20),
            genre="F",
            parent_id=self.parent_autre.id,
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_act_2.id,
        )
        db.session.add_all([self.eleve, self.eleve_autre])
        db.session.flush()

        self.ins_arch = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe_arch.id,
            annee_scolaire_id=self.archivee.id,
            ecole_id=self.ecole_a.id,
            statut="active",
        )
        self.ins_act = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe_act.id,
            annee_scolaire_id=self.active.id,
            ecole_id=self.ecole_a.id,
            statut="active",
        )
        self.ins_act_autre = Inscription(
            eleve_id=self.eleve_autre.id,
            classe_id=self.classe_act_2.id,
            annee_scolaire_id=self.active.id,
            ecole_id=self.ecole_a.id,
            statut="active",
        )
        db.session.add_all([self.ins_arch, self.ins_act, self.ins_act_autre])
        db.session.flush()

        # 8. Créneaux initiaux
        # Créneau archivé
        self.creneau_arch = EmploiTemps(
            classe_id=self.classe_arch.id,
            cours_id=self.cours_arch.id,
            professeur_id=self.prof_1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
            salle="Salle 1",
            ecole_id=self.ecole_a.id,
        )
        # Créneau actif
        self.creneau_act = EmploiTemps(
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_1.id,
            professeur_id=self.prof_1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
            salle="A101",
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.creneau_arch, self.creneau_act])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # -------------------------------------------------------------
    # 1. Structure & Intégrité relationnelle
    # -------------------------------------------------------------
    def test_01_creneau_rattache_classe_annuelle(self):
        self.assertEqual(self.creneau_act.classe_id, self.classe_act.id)
        self.assertEqual(self.creneau_act.classe.annee_scolaire_id, self.active.id)
        self.assertEqual(self.creneau_act.annee_scolaire_id, self.active.id)
        self.assertEqual(self.creneau_act.annee_scolaire.nom, "2025-2026")

    def test_02_cours_coherent_avec_classe(self):
        # Tenter d'associer un cours d'une autre classe
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_2nd.id,  # cours de classe_act_2
            professeur_id=self.prof_1.id,
            jour="Mardi",
            heure_debut=time(10, 0),
            heure_fin=time(11, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Le cours doit obligatoirement appartenir à la classe sélectionnée", err)

    def test_03_ecole_coherente(self):
        # Tenter d'utiliser une classe de l'école B
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_b.id,
            cours_id=self.cours_b.id,
            professeur_id=self.prof_b.id,
            jour="Mardi",
            heure_debut=time(10, 0),
            heure_fin=time(11, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Classe introuvable pour votre établissement", err)

    # -------------------------------------------------------------
    # 2. Cycle de vie : Année ACTIVE (CRUD)
    # -------------------------------------------------------------
    def test_04_active_creation_succes(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_2.id,
            professeur_id=self.prof_2.id,
            jour="Mardi",
            heure_debut=time(10, 0),
            heure_fin=time(11, 30),
            salle="Labo 1",
        )
        self.assertIsNotNone(creneau)
        self.assertIsNone(err)
        self.assertEqual(creneau.jour, "Mardi")
        self.assertEqual(creneau.salle, "Labo 1")

    def test_05_active_modification_succes(self):
        creneau, err = valider_et_modifier_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            creneau_id=self.creneau_act.id,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_1.id,
            professeur_id=self.prof_1.id,
            jour="Jeudi",
            heure_debut=time(14, 0),
            heure_fin=time(15, 0),
            salle="B202",
        )
        self.assertIsNotNone(creneau)
        self.assertIsNone(err)
        self.assertEqual(creneau.jour, "Jeudi")
        self.assertEqual(creneau.salle, "B202")

    def test_06_active_suppression_succes(self):
        ok, err = supprimer_creneau(self.ecole_a.id, self.active, self.creneau_act.id)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIsNone(EmploiTemps.query.get(self.creneau_act.id))

    # -------------------------------------------------------------
    # 3. Cycle de vie : Année PLANIFIÉE (Préparation autorisée)
    # -------------------------------------------------------------
    def test_07_planifiee_creation_autorisee(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(10, 0),
            salle="Salle Planifiée",
        )
        self.assertIsNotNone(creneau)
        self.assertIsNone(err)
        self.assertEqual(creneau.classe.annee_scolaire_id, self.planifiee.id)

    def test_08_planifiee_modification_autorisee(self):
        creneau, _ = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        mod, err = valider_et_modifier_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            creneau_id=creneau.id,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Vendredi",
            heure_debut=time(9, 0),
            heure_fin=time(10, 0),
        )
        self.assertIsNotNone(mod)
        self.assertIsNone(err)
        self.assertEqual(mod.jour, "Vendredi")

    def test_09_planifiee_suppression_autorisee(self):
        creneau, _ = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        ok, err = supprimer_creneau(self.ecole_a.id, self.planifiee, creneau.id)
        self.assertTrue(ok)
        self.assertIsNone(err)

    # -------------------------------------------------------------
    # 4. Cycle de vie : Année ARCHIVÉE (Lecture seule stricte)
    # -------------------------------------------------------------
    def test_10_archive_consultation_autorisee(self):
        creneaux = get_creneaux_annee(self.ecole_a.id, self.archivee)
        self.assertEqual(len(creneaux), 1)
        self.assertEqual(creneaux[0].id, self.creneau_arch.id)

    def test_11_archive_creation_refusee(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            classe_id=self.classe_arch.id,
            cours_id=self.cours_arch.id,
            professeur_id=self.prof_1.id,
            jour="Mardi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

    def test_12_archive_modification_refusee(self):
        mod, err = valider_et_modifier_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            creneau_id=self.creneau_arch.id,
            classe_id=self.classe_arch.id,
            cours_id=self.cours_arch.id,
            professeur_id=self.prof_1.id,
            jour="Mardi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(mod)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

    def test_13_archive_suppression_refusee(self):
        ok, err = supprimer_creneau(self.ecole_a.id, self.archivee, self.creneau_arch.id)
        self.assertFalse(ok)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)
        self.assertIsNotNone(EmploiTemps.query.get(self.creneau_arch.id))

    # -------------------------------------------------------------
    # 5. Cohérence inter-entités et validation
    # -------------------------------------------------------------
    def test_14_classe_autre_annee_refusee(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_arch.id,  # classe archivée
            cours_id=self.cours_arch.id,
            professeur_id=self.prof_1.id,
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("La classe n'appartient pas à l'année scolaire consultée", err)

    def test_15_classe_autre_ecole_refusee(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_b.id,
            cours_id=self.cours_b.id,
            professeur_id=self.prof_b.id,
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Classe introuvable", err)

    def test_16_cours_autre_classe_refuse(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_2nd.id,
            professeur_id=self.prof_1.id,
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Le cours doit obligatoirement appartenir à la classe", err)

    def test_17_cours_autre_annee_refuse(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_arch.id,
            professeur_id=self.prof_1.id,
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Le cours doit obligatoirement appartenir à la classe", err)

    def test_18_cours_autre_ecole_refuse(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_b.id,
            professeur_id=self.prof_1.id,
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Cours introuvable pour votre établissement", err)

    def test_19_professeur_autre_ecole_refuse(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_2.id,
            professeur_id=self.prof_b.id,  # prof école B
            jour="Mercredi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("Enseignant introuvable", err)

    def test_20_heure_debut_apres_heure_fin_rejetee(self):
        creneau, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            classe_id=self.classe_act.id,
            cours_id=self.cours_act_1.id,
            professeur_id=self.prof_1.id,
            jour="Mercredi",
            heure_debut=time(11, 0),
            heure_fin=time(10, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("L'heure de début doit être strictement antérieure à l'heure de fin", err)

    # -------------------------------------------------------------
    # 6. Conflits horaires & Règle de chevauchement
    # -------------------------------------------------------------
    def test_21_conflit_meme_classe_detecte(self):
        # classe_act a déjà Lundi 08:00-09:00
        # On tente d'ajouter Lundi 08:30-09:30
        has_conflict, msg = detecter_conflits(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            jour="Lundi",
            heure_debut=time(8, 30),
            heure_fin=time(9, 30),
            classe_id=self.classe_act.id,
            professeur_id=self.prof_2.id,
        )
        self.assertTrue(has_conflict)
        self.assertIn("Conflit de classe", msg)

    def test_22_conflit_meme_professeur_detecte(self):
        # prof_1 est en cours le Lundi 08:00-09:00 dans classe_act
        # On tente d'ajouter un cours pour prof_1 dans classe_act_2 le Lundi 08:30-09:30
        has_conflict, msg = detecter_conflits(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            jour="Lundi",
            heure_debut=time(8, 30),
            heure_fin=time(9, 30),
            classe_id=self.classe_act_2.id,
            professeur_id=self.prof_1.id,
        )
        self.assertTrue(has_conflict)
        self.assertIn("Conflit enseignant", msg)

    def test_23_conflit_meme_salle_detecte(self):
        # salle A101 est occupée Lundi 08:00-09:00
        # On tente de l'affecter à classe_act_2 avec prof_2 sur 08:15-09:15
        has_conflict, msg = detecter_conflits(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            jour="Lundi",
            heure_debut=time(8, 15),
            heure_fin=time(9, 15),
            classe_id=self.classe_act_2.id,
            professeur_id=self.prof_2.id,
            salle="A101",
        )
        self.assertTrue(has_conflict)
        self.assertIn("Conflit de salle", msg)

    def test_24_creneaux_adjacents_autorises(self):
        # 08:00-09:00 existe déjà
        # 09:00-10:00 doit être autorisé (pas de chevauchement strict)
        has_conflict, msg = detecter_conflits(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            jour="Lundi",
            heure_debut=time(9, 0),
            heure_fin=time(10, 0),
            classe_id=self.classe_act.id,
            professeur_id=self.prof_1.id,
            salle="A101",
        )
        self.assertFalse(has_conflict)
        self.assertIsNone(msg)

    def test_25_conflit_autre_annee_ignore(self):
        # creneau_arch est Lundi 08:00-09:00 dans archivee
        # Dans active, Lundi 08:00-09:00 dans une autre classe ne doit pas entrer en conflit avec archivee
        has_conflict, msg = detecter_conflits(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
            classe_id=self.classe_act_2.id,
            professeur_id=self.prof_2.id,
            salle="Salle 1",  # salle utilisée dans l'année archivée
        )
        self.assertFalse(has_conflict)
        self.assertIsNone(msg)

    # -------------------------------------------------------------
    # 7. Cloisonnement des plannings
    # -------------------------------------------------------------
    def test_26_planning_annee_A_isole(self):
        creneaux = get_creneaux_annee(self.ecole_a.id, self.archivee)
        self.assertEqual(len(creneaux), 1)
        self.assertEqual(creneaux[0].cours.nom, "Mathématiques Archivées")

    def test_27_planning_annee_B_isole(self):
        creneaux = get_creneaux_annee(self.ecole_a.id, self.active)
        self.assertEqual(len(creneaux), 1)
        self.assertEqual(creneaux[0].cours.nom, "Mathématiques Actives")

    # -------------------------------------------------------------
    # 8. Règle 2C-5D : Immutabilité de session["annee_consultee"]
    # -------------------------------------------------------------
    def test_28_regle_2c5d_get_ne_change_pas_session(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.active.id)

            resp = client.get('/admin/emplois')
            self.assertEqual(resp.status_code, 200)

            with client.session_transaction() as sess:
                self.assertEqual(sess.get('annee_consultee'), str(self.active.id))

    def test_29_regle_2c5d_post_ne_change_pas_session(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.active.id)

            resp = client.post('/admin/ajouter_emploi', data={
                'classe_id': self.classe_act.id,
                'cours_id': self.cours_act_2.id,
                'professeur_id': self.prof_2.id,
                'jour': 'Mardi',
                'heure_debut': '14:00',
                'heure_fin': '15:00',
                'salle': 'C10',
            })
            self.assertEqual(resp.status_code, 302)

            with client.session_transaction() as sess:
                self.assertEqual(sess.get('annee_consultee'), str(self.active.id))

    def test_30_regle_2c5d_param_annee_id_ignore(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.active.id)

            resp = client.get(f'/admin/emplois?annee_id={self.archivee.id}')
            self.assertEqual(resp.status_code, 200)

            with client.session_transaction() as sess:
                self.assertEqual(sess.get('annee_consultee'), str(self.active.id))

    # -------------------------------------------------------------
    # 9. Rôles & Permissions : Parent & Découplage Eleve.classe_id
    # -------------------------------------------------------------
    def test_31_parent_voit_classe_propre_enfant(self):
        classes = get_classes_pour_utilisateur(self.ecole_a.id, self.active, self.parent)
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0].id, self.classe_act.id)

    def test_32_parent_autre_classe_refuse(self):
        # Le parent de Moussa tente d'accéder aux données de la classe de Fatou
        donnees, err = donnees_impression_classe(self.ecole_a.id, self.active, self.classe_act_2.id, user=self.parent)
        self.assertIsNone(donnees)
        self.assertIn("Accès non autorisé", err)

    def test_33_parent_eleve_classe_id_nul_historique_fonctionne(self):
        # Découplage strict : Eleve.classe_id = None
        self.eleve.classe_id = None
        db.session.commit()

        classes = get_classes_pour_utilisateur(self.ecole_a.id, self.archivee, self.parent)
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0].id, self.classe_arch.id)

        donnees, err = donnees_impression_classe(self.ecole_a.id, self.archivee, self.classe_arch.id, user=self.parent)
        self.assertIsNotNone(donnees)
        self.assertIsNone(err)

    def test_34_parent_eleve_classe_id_different_sans_impact(self):
        # Élève déplacé vers une classe différente (ex: classe_b ou classe suivante)
        self.eleve.classe_id = self.classe_plan.id
        db.session.commit()

        # En consultant l'année active, l'inscription active donne classe_act
        classes = get_classes_pour_utilisateur(self.ecole_a.id, self.active, self.parent)
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0].id, self.classe_act.id)

    # -------------------------------------------------------------
    # 10. Rôles & Permissions : Professeur
    # -------------------------------------------------------------
    def test_35_professeur_voit_ses_creneaux(self):
        classes = get_classes_pour_utilisateur(self.ecole_a.id, self.active, self.prof_user_1)
        self.assertIn(self.classe_act.id, [c.id for c in classes])
        self.assertIn(self.classe_act_2.id, [c.id for c in classes])

        creneaux = get_creneaux_annee(self.ecole_a.id, self.active, professeur_id=self.prof_1.id)
        self.assertEqual(len(creneaux), 1)
        self.assertEqual(creneaux[0].professeur_id, self.prof_1.id)

    def test_36_professeur_autre_perimetre_refuse(self):
        # Prof 2 n'intervient pas dans classe_act_2
        classes = get_classes_pour_utilisateur(self.ecole_a.id, self.active, self.prof_user_2)
        self.assertIn(self.classe_act.id, [c.id for c in classes])
        self.assertNotIn(self.classe_act_2.id, [c.id for c in classes])

    # -------------------------------------------------------------
    # 11. Multi-écoles & Sécurité IDs forgés
    # -------------------------------------------------------------
    def test_37_admin_ecole_A_isole_de_ecole_B(self):
        creneaux_a = get_creneaux_annee(self.ecole_a.id, self.active)
        self.assertTrue(all(c.ecole_id == self.ecole_a.id for c in creneaux_a))

    def test_38_id_forge_bloque(self):
        # Créneau de l'école B
        creneau_b = EmploiTemps(
            classe_id=self.classe_b.id,
            cours_id=self.cours_b.id,
            professeur_id=self.prof_b.id,
            jour="Lundi",
            heure_debut=time(8, 0),
            heure_fin=time(9, 0),
            ecole_id=self.ecole_b.id,
        )
        db.session.add(creneau_b)
        db.session.commit()

        # Admin de l'école A tente de modifier ou supprimer le créneau B
        ok, err = supprimer_creneau(self.ecole_a.id, self.active, creneau_b.id)
        self.assertFalse(ok)
        self.assertIn("introuvable", err)

    # -------------------------------------------------------------
    # 12. Activation d'année & Nouvelle année
    # -------------------------------------------------------------
    def test_39_activation_conserve_creneaux_planifies(self):
        creneau_p, _ = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Mardi",
            heure_debut=time(10, 0),
            heure_fin=time(11, 0),
        )
        # Activation de l'année planifiée
        self.planifiee.statut = "active"
        db.session.commit()

        # Les créneaux préparés restent intacts
        creneaux_post = get_creneaux_annee(self.ecole_a.id, self.planifiee)
        self.assertEqual(len(creneaux_post), 1)
        self.assertEqual(creneaux_post[0].id, creneau_p.id)

    def test_40_activation_ne_duplique_pas_creneaux(self):
        total_avant = EmploiTemps.query.count()
        # Simulation bascule
        self.active.statut = "archivee"
        self.planifiee.statut = "active"
        db.session.commit()
        total_apres = EmploiTemps.query.count()
        self.assertEqual(total_avant, total_apres)

    def test_41_nouvelle_annee_ne_copie_pas_edt_automatiquement(self):
        annee_futur = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(annee_futur)
        db.session.commit()

        creneaux_futur = get_creneaux_annee(self.ecole_a.id, annee_futur)
        self.assertEqual(len(creneaux_futur), 0)

    # -------------------------------------------------------------
    # 13. Export & Impression
    # -------------------------------------------------------------
    def test_42_export_json_et_imprimer_annee_correcte(self):
        donnees, err = donnees_impression_classe(self.ecole_a.id, self.active, self.classe_act.id)
        self.assertIsNone(err)
        self.assertEqual(donnees["annee_scolaire_nom"], "2025-2026")
        self.assertEqual(donnees["classe_nom"], "5ème B (25-26)")
        self.assertEqual(len(donnees["creneaux"]), 1)
        self.assertEqual(donnees["creneaux"][0]["cours_nom"], "Mathématiques Actives")

    # -------------------------------------------------------------
    # 14. Routes HTTP Mutations
    # -------------------------------------------------------------
    def test_43_http_modifier_emploi_annee_active_succes(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.active.id)

            resp = client.post(f'/emploi/{self.creneau_act.id}/modifier', data={
                'classe_id': self.classe_act.id,
                'cours_id': self.cours_act_1.id,
                'professeur_id': self.prof_1.id,
                'jour': 'Jeudi',
                'heure_debut': '10:00',
                'heure_fin': '11:00',
                'salle': 'D40',
            })
            self.assertEqual(resp.status_code, 302)
            db.session.refresh(self.creneau_act)
            self.assertEqual(self.creneau_act.jour, 'Jeudi')
            self.assertEqual(self.creneau_act.salle, 'D40')

    def test_44_http_modifier_emploi_annee_archivee_rejetee(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.archivee.id)

            resp = client.post(f'/emploi/{self.creneau_arch.id}/modifier', data={
                'classe_id': self.classe_arch.id,
                'cours_id': self.cours_arch.id,
                'professeur_id': self.prof_1.id,
                'jour': 'Jeudi',
                'heure_debut': '10:00',
                'heure_fin': '11:00',
            })
            self.assertEqual(resp.status_code, 302)
            db.session.refresh(self.creneau_arch)
            self.assertEqual(self.creneau_arch.jour, 'Lundi')  # inchangé

    def test_45_http_supprimer_emploi_annee_active_succes(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.active.id)

            resp = client.post(f'/emploi/{self.creneau_act.id}/supprimer')
            self.assertEqual(resp.status_code, 302)
            self.assertIsNone(EmploiTemps.query.get(self.creneau_act.id))

    def test_46_http_supprimer_emploi_annee_archivee_rejetee(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['role'] = 'admin'
                sess['ecole_id'] = self.ecole_a.id
                sess['annee_consultee'] = str(self.archivee.id)

            resp = client.post(f'/emploi/{self.creneau_arch.id}/supprimer')
            self.assertEqual(resp.status_code, 302)
            self.assertIsNotNone(EmploiTemps.query.get(self.creneau_arch.id))

    # -------------------------------------------------------------
    # 15. Scénario End-to-End A/B (Section 30 du Prompt)
    # -------------------------------------------------------------
    def test_47_scenario_end_to_end_A_B(self):
        """
        Année A = archivee (6e A, Math A, Lundi 08:00-09:00)
        Année B = planifiee (5e B, Math B, Mardi 10:00-11:00)
        Consultation A : uniquement ancien créneau, lecture seule
        Consultation B : uniquement nouveau créneau, modification autorisée
        Activer B : même créneau mardi 10:00, aucune duplication, aucune copie depuis A.
        """
        # Créneau Année B planifiée
        creneau_b, err = valider_et_creer_creneau(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_plan.id,
            professeur_id=self.prof_1.id,
            jour="Mardi",
            heure_debut=time(10, 0),
            heure_fin=time(11, 0),
        )
        self.assertIsNotNone(creneau_b)
        self.assertIsNone(err)

        # 1. Consultation A (archivee)
        creneaux_a = get_creneaux_annee(self.ecole_a.id, self.archivee)
        self.assertEqual(len(creneaux_a), 1)
        self.assertEqual(creneaux_a[0].jour, "Lundi")
        self.assertEqual(creneaux_a[0].heure_debut, time(8, 0))
        self.assertFalse(peut_modifier_emploi_temps(self.archivee))

        # 2. Consultation B (planifiee)
        creneaux_b = get_creneaux_annee(self.ecole_a.id, self.planifiee)
        self.assertEqual(len(creneaux_b), 1)
        self.assertEqual(creneaux_b[0].jour, "Mardi")
        self.assertEqual(creneaux_b[0].heure_debut, time(10, 0))
        self.assertTrue(peut_modifier_emploi_temps(self.planifiee))

        # 3. Activation de B
        count_avant = EmploiTemps.query.count()
        self.planifiee.statut = "active"
        db.session.commit()
        count_apres = EmploiTemps.query.count()
        self.assertEqual(count_avant, count_apres)

        # Après activation
        creneaux_b_apres = get_creneaux_annee(self.ecole_a.id, self.planifiee)
        self.assertEqual(len(creneaux_b_apres), 1)
        self.assertEqual(creneaux_b_apres[0].id, creneau_b.id)
        self.assertEqual(creneaux_b_apres[0].jour, "Mardi")
        self.assertEqual(creneaux_b_apres[0].heure_debut, time(10, 0))

    # -------------------------------------------------------------
    # 16. Vraie Base SQLite instance/ecole.db
    # -------------------------------------------------------------
    def test_48_vraie_db_sqlite_schema_et_creneau_annuel(self):
        db_path = os.path.join(self.app.root_path, "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base instance/ecole.db non présente")

        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Vérification colonnes
        c.execute("PRAGMA table_info(emploi_temps)")
        cols = {row[1]: row[2] for row in c.fetchall()}
        self.assertIn("id", cols)
        self.assertIn("classe_id", cols)
        self.assertIn("cours_id", cols)
        self.assertIn("professeur_id", cols)
        self.assertIn("jour", cols)
        self.assertIn("heure_debut", cols)
        self.assertIn("heure_fin", cols)

        # Vérification clés étrangères
        c.execute("PRAGMA foreign_key_list(emploi_temps)")
        fks = {row[2]: row[3] for row in c.fetchall()}
        self.assertIn("classe", fks)
        self.assertIn("cours", fks)
        self.assertIn("professeur", fks)

        conn.close()


if __name__ == "__main__":
    unittest.main()
