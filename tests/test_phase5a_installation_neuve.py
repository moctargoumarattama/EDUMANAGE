import unittest
from datetime import date
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.cli import _execute_technical_init
from app.config import Config
from app.init_superadmin import ensure_canonical_superadmin
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    EcoleNiveauConfig,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
)
from app.services.niveaux import (
    STANDARD_NIVEAUX,
    creer_classe_depuis_niveau,
    ensure_standard_niveaux,
)
from app.services.structure_annuelle import sauvegarder_structure_annee
from app.startup import corriger_donnees
from app.utils import get_school_setup_state


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase5AInstallationNeuveTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_01_catalogue_standard_complet(self):
        """Le catalogue NiveauScolaire contient exactement les 13 niveaux standards avec leurs cycles."""
        niveaux = ensure_standard_niveaux(commit=True)
        self.assertEqual(len(niveaux), 13)

        codes_attendus = [
            "CI", "CP", "CE1", "CE2", "CM1", "CM2",
            "6E", "5E", "4E", "3E",
            "2NDE", "1ERE", "TERMINALE"
        ]
        codes_db = [n.code for n in NiveauScolaire.query.order_by(NiveauScolaire.ordre).all()]
        self.assertEqual(codes_db, codes_attendus)

        # Vérification des cycles
        cycles = {n.code: n.cycle for n in NiveauScolaire.query.all()}
        self.assertEqual(cycles["CI"], "primaire")
        self.assertEqual(cycles["CM2"], "primaire")
        self.assertEqual(cycles["6E"], "college")
        self.assertEqual(cycles["3E"], "college")
        self.assertEqual(cycles["2NDE"], "lycee")
        self.assertEqual(cycles["TERMINALE"], "lycee")

        # Vérification du chaînage niveau_suivant_id
        n_6e = NiveauScolaire.query.filter_by(code="6E").first()
        n_5e = NiveauScolaire.query.filter_by(code="5E").first()
        self.assertIsNotNone(n_6e.niveau_suivant_id)
        self.assertEqual(n_6e.niveau_suivant_id, n_5e.id)

    def test_02_idempotence_catalogue_standard(self):
        """L'appel répété de ensure_standard_niveaux ne crée aucun doublon et conserve les IDs."""
        niveaux_1 = ensure_standard_niveaux(commit=True)
        ids_1 = [n.id for n in niveaux_1]

        niveaux_2 = ensure_standard_niveaux(commit=True)
        ids_2 = [n.id for n in niveaux_2]

        self.assertEqual(ids_1, ids_2)
        self.assertEqual(NiveauScolaire.query.count(), 13)

    def test_03_ecole_niveau_config_non_requis(self):
        """EcoleNiveauConfig n'est pas requis pour le fonctionnement du système."""
        ensure_standard_niveaux(commit=True)
        self.assertEqual(EcoleNiveauConfig.query.count(), 0)

    def test_04_purete_initialisation_technique(self):
        """L'initialisation technique ne crée aucune entité métier (école, classe, élève, etc.)."""
        _execute_technical_init(quiet=True)

        self.assertEqual(NiveauScolaire.query.count(), 13)
        self.assertEqual(Utilisateur.query.filter_by(role="super_admin").count(), 1)
        self.assertEqual(Ecole.query.count(), 0)
        self.assertEqual(Classe.query.count(), 0)
        self.assertEqual(Eleve.query.count(), 0)
        self.assertEqual(Professeur.query.count(), 0)
        self.assertEqual(Inscription.query.count(), 0)
        self.assertEqual(Note.query.count(), 0)
        self.assertEqual(Paiement.query.count(), 0)

    def test_05_ecole_neuve_onboarding_incomplet(self):
        """Une nouvelle école sans année scolaire est détectée en étape 'year'."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Complexe Scolaire Sahel", adresse="Niamey", email="sahel@klasora.ne")
        db.session.add(ecole)
        db.session.commit()

        state = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertFalse(state["setup_complete"])
        self.assertFalse(state["has_active_year"])
        self.assertEqual(state["current_step"], "year")

    def test_06_creation_premiere_annee_scolaire(self):
        """Après création de l'année scolaire active, l'onboarding passe à l'étape 'pedagogie'."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Complexe Scolaire Sahel", adresse="Niamey")
        db.session.add(ecole)
        db.session.commit()

        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()

        state = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertFalse(state["setup_complete"])
        self.assertTrue(state["has_active_year"])
        self.assertFalse(state["has_pedagogie"])
        self.assertEqual(state["current_step"], "pedagogie")

    def test_07_configuration_structure_annuelle_complete_onboarding(self):
        """Après configuration de la structure annuelle (AnneeNiveauConfig), l'onboarding est validé."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Complexe Scolaire Sahel", adresse="Niamey")
        db.session.add(ecole)
        db.session.commit()

        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()

        n_6e = NiveauScolaire.query.filter_by(code="6E").first()
        n_5e = NiveauScolaire.query.filter_by(code="5E").first()
        n_2nde = NiveauScolaire.query.filter_by(code="2NDE").first()

        # Configuration annuelle
        sauvegarder_structure_annee(
            ecole.id,
            annee.id,
            [n_6e.id, n_5e.id, n_2nde.id],
        )

        state = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertTrue(state["setup_complete"])
        self.assertTrue(state["has_active_year"])
        self.assertTrue(state["has_pedagogie"])
        self.assertEqual(state["current_step"], "complete")

    def test_08_routes_principales_accessibles_sans_boucle(self):
        """Avec onboarding complet, les routes clés de l'application répondent en 200 sans redirection onboarding."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Complexe Scolaire Sahel", adresse="Niamey")
        db.session.add(ecole)
        db.session.commit()

        admin = Utilisateur(
            nom="Admin",
            prenom="Test",
            email="admin@sahel.ne",
            mot_de_passe=generate_password_hash("password123"),
            role="admin",
            statut="actif",
            ecole_id=ecole.id,
        )
        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add_all([admin, annee])
        db.session.commit()

        n_6e = NiveauScolaire.query.filter_by(code="6E").first()
        sauvegarder_structure_annee(ecole.id, annee.id, [n_6e.id])

        # Login admin
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True

        routes = ["/", "/annees", "/classes", "/eleves", "/professeurs"]
        for route in routes:
            resp = self.client.get(route, follow_redirects=False)
            self.assertEqual(
                resp.status_code,
                200,
                f"La route {route} a renvoyé {resp.status_code} au lieu de 200 (location: {resp.headers.get('Location')})"
            )

    def test_09_creation_classes_dans_structure(self):
        """Création valide de classes pour les niveaux configurés dans AnneeNiveauConfig."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Lycée Moderne", adresse="Maradi")
        db.session.add(ecole)
        db.session.commit()

        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()

        n_6e = NiveauScolaire.query.filter_by(code="6E").first()
        n_5e = NiveauScolaire.query.filter_by(code="5E").first()
        n_2nde = NiveauScolaire.query.filter_by(code="2NDE").first()

        sauvegarder_structure_annee(
            ecole.id,
            annee.id,
            [n_6e.id, n_5e.id, n_2nde.id],
        )

        # Création 6e A
        c6, err6 = creer_classe_depuis_niveau(
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
            niveau_id=n_6e.id,
            section="A",
        )
        self.assertIsNone(err6)
        self.assertIsNotNone(c6)
        self.assertEqual(c6.nom, "6e A")

        # Création 5e A
        c5, err5 = creer_classe_depuis_niveau(
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
            niveau_id=n_5e.id,
            section="A",
        )
        self.assertIsNone(err5)
        self.assertIsNotNone(c5)
        self.assertEqual(c5.nom, "5e A")

        # Création 2nde Série A
        c2, err2 = creer_classe_depuis_niveau(
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
            niveau_id=n_2nde.id,
            section="A",
        )
        self.assertIsNone(err2)
        self.assertIsNotNone(c2)
        self.assertEqual(c2.nom, "2nde Serie A")


    def test_10_refus_strict_classe_hors_structure(self):
        """Refus strict de création de classe si le niveau n'est pas retenu dans la structure annuelle."""
        ensure_standard_niveaux(commit=True)
        ecole = Ecole(nom="Lycée Moderne", adresse="Maradi")
        db.session.add(ecole)
        db.session.commit()

        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()

        n_6e = NiveauScolaire.query.filter_by(code="6E").first()
        n_tle = NiveauScolaire.query.filter_by(code="TERMINALE").first()

        # Structure ne contient QUE 6e
        sauvegarder_structure_annee(ecole.id, annee.id, [n_6e.id])

        # Tentative de création pour Terminale (hors structure)
        c_tle, err_tle = creer_classe_depuis_niveau(
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
            niveau_id=n_tle.id,
            section="A",
        )
        self.assertIsNone(c_tle)
        self.assertIsNotNone(err_tle)
        self.assertIn("pas retenu", err_tle)

    def test_11_isolation_multi_ecoles(self):
        """Deux écoles distinctes ont des structures et des classes totalement isolées."""
        ensure_standard_niveaux(commit=True)
        ecole_1 = Ecole(nom="Ecole 1")
        ecole_2 = Ecole(nom="Ecole 2")
        db.session.add_all([ecole_1, ecole_2])
        db.session.commit()

        annee_1 = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_1.id,
        )
        annee_2 = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole_2.id,
        )
        db.session.add_all([annee_1, annee_2])
        db.session.commit()

        n_ci = NiveauScolaire.query.filter_by(code="CI").first()
        n_6e = NiveauScolaire.query.filter_by(code="6E").first()

        # Ecole 1 configure 6e, Ecole 2 configure CI
        sauvegarder_structure_annee(ecole_1.id, annee_1.id, [n_6e.id])
        sauvegarder_structure_annee(ecole_2.id, annee_2.id, [n_ci.id])


        # Ecole 1 ne peut pas créer CI
        c_fail, err = creer_classe_depuis_niveau(ecole_1.id, annee_1.id, n_ci.id, section="A")
        self.assertIsNone(c_fail)
        self.assertIsNotNone(err)

        # Ecole 2 ne peut pas créer 6e
        c_fail2, err2 = creer_classe_depuis_niveau(ecole_2.id, annee_2.id, n_6e.id, section="A")
        self.assertIsNone(c_fail2)
        self.assertIsNotNone(err2)

        # Ecole 1 crée 6e A avec succès
        c_ok, _ = creer_classe_depuis_niveau(ecole_1.id, annee_1.id, n_6e.id, section="A")
        self.assertIsNotNone(c_ok)
        self.assertEqual(Classe.query.filter_by(ecole_id=ecole_2.id).count(), 0)

    def test_12_startup_ne_cree_pas_de_donnees_fictives(self):
        """corriger_donnees() est inerte et ne fabrique jamais d'école ni de classe fictive."""
        self.assertEqual(Ecole.query.count(), 0)
        self.assertEqual(Classe.query.count(), 0)

        corriger_donnees()

        self.assertEqual(Ecole.query.count(), 0)
        self.assertEqual(Classe.query.count(), 0)

    def test_13_cli_runner_init_system_et_alias(self):
        """Les commandes CLI 'init-system' et 'init-technical-data' s'exécutent avec succès."""
        runner = self.app.test_cli_runner()

        # Commande principale
        res1 = runner.invoke(args=["init-system"])
        self.assertEqual(res1.exit_code, 0)
        self.assertIn("13 niveaux configur", res1.output)
        self.assertIn("Super administrateur canonique", res1.output)

        # Alias
        res2 = runner.invoke(args=["init-technical-data"])
        self.assertEqual(res2.exit_code, 0)
        self.assertIn("13 niveaux configur", res2.output)

    def test_14_processus_complet_installation_neuve(self):
        """Cycle complet de bout en bout d'une installation neuve : technique -> école -> année -> structure -> classes -> prof."""
        # 1. Init technique
        _execute_technical_init(quiet=True)
        self.assertEqual(NiveauScolaire.query.count(), 13)
        self.assertEqual(Ecole.query.count(), 0)

        # 2. Création école + admin
        ecole = Ecole(nom="Institut d'Excellence", adresse="Niamey Plateau", telephone="+22720202020")
        db.session.add(ecole)
        db.session.commit()

        admin = Utilisateur(
            nom="Directeur",
            prenom="Oumarou",
            email="directeur@excellence.ne",
            mot_de_passe=generate_password_hash("adminPass123"),
            role="admin",
            statut="actif",
            ecole_id=ecole.id,
        )
        db.session.add(admin)
        db.session.commit()

        # Onboarding étape 1 (year)
        state1 = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertFalse(state1["setup_complete"])
        self.assertEqual(state1["current_step"], "year")

        # 3. Création 1ère année active
        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 15),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()

        # Onboarding étape 2 (pedagogie)
        state2 = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertFalse(state2["setup_complete"])
        self.assertEqual(state2["current_step"], "pedagogie")

        # 4. Configuration structure annuelle (6e, 5e, 4e, 3e)
        college_codes = ["6E", "5E", "4E", "3E"]
        college_niveaux = NiveauScolaire.query.filter(NiveauScolaire.code.in_(college_codes)).all()
        sauvegarder_structure_annee(ecole.id, annee.id, [n.id for n in college_niveaux])

        # Onboarding terminé
        state3 = get_school_setup_state(ecole.id, force_refresh=True)
        self.assertTrue(state3["setup_complete"])
        self.assertEqual(state3["current_step"], "complete")

        # 5. Création des classes
        for n in college_niveaux:
            classe, err = creer_classe_depuis_niveau(ecole.id, annee.id, n.id, section="A")
            self.assertIsNone(err)
            self.assertIsNotNone(classe)

        self.assertEqual(Classe.query.filter_by(ecole_id=ecole.id).count(), 4)

        # 6. Ajout professeur et affectation
        prof_user = Utilisateur(
            nom="Ibrahim",
            prenom="Moussa",
            email="ibrahim@excellence.ne",
            mot_de_passe=generate_password_hash("profPass123"),
            role="professeur",
            statut="actif",
            ecole_id=ecole.id,
        )
        db.session.add(prof_user)
        db.session.commit()

        prof = Professeur(
            nom="Ibrahim",
            prenom="Moussa",
            email="ibrahim@excellence.ne",
            ecole_id=ecole.id,
            utilisateur_id=prof_user.id,
        )
        db.session.add(prof)
        db.session.commit()

        classe_6e = Classe.query.filter_by(ecole_id=ecole.id, nom="6e A").first()
        from datetime import datetime
        from app.models import professeur_classes
        db.session.execute(
            professeur_classes.insert().values(
                professeur_id=prof.id,
                classe_id=classe_6e.id,
                ecole_id=ecole.id,
                date_assignation=datetime.utcnow()
            )
        )
        db.session.commit()

        self.assertEqual(prof.classes_assignees.count(), 1)
        self.assertEqual(prof.classes_assignees.first().nom, "6e A")




