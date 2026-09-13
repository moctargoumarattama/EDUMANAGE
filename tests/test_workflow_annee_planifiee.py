"""
tests/test_workflow_annee_planifiee.py
=============================================================
KLASORA — Tests d'architecture et de workflow de l'année planifiée.
Respect strict de la règle 2C-5D :
SEUL le module /annees peut modifier session["annee_consultee"].
Les modules /classes, /classes/add, /eleves, /cours ne font que
consommer ce contexte.

11 Vérifications obligatoires :
1. POST /annees/<planifiee>/consulter -> session change ;
2. GET /classes -> utilise ensuite cette année ;
3. GET /classes/add -> utilise la même année ;
4. POST /classes/add -> crée la classe dans cette année ;
5. POST /classes/add -> NE modifie pas annee_consultee ;
6. annee_id=<autre> -> ne change pas le contexte ;
7. aucune année en session -> fallback sur année active ;
8. ne jamais fallback sur "dernière non archivée" ;
9. archivee toujours bloquée ;
10. planifiee autorisée (structure, création de classe, passage annuel) ;
11. multi-école protégée.
=============================================================
"""

import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle, get_inscription
from app.services.niveaux import (
    ensure_standard_niveaux,
    ensure_ecole_niveau_configs,
    creer_classe_depuis_niveau,
)
from app.services.niveaux_annuels import (
    get_selection_annuelle,
    sauvegarder_selection_annuelle,
)
from app.services.classes_annuelles import set_classe_ouverte
from app.services.passage_annee import (
    get_classes_candidates_passage,
    executer_passage_eleve,
)
from app.services.annees_scolaires import get_annee_consultee, set_annee_consultee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class TestWorkflowAnneePlanifiee(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        self.niv = {n.code: n for n in niveaux}

        # Écoles
        self.ecole_a = Ecole(nom="Groupe Scolaire Alpha")
        self.ecole_b = Ecole(nom="Groupe Scolaire Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        ensure_ecole_niveau_configs(self.ecole_a.id, default_active=True, commit=False)
        ensure_ecole_niveau_configs(self.ecole_b.id, default_active=True, commit=False)

        # Années école A : 2024-2025 (archivée), 2025-2026 (active), 2026-2027 (planifiée)
        self.annee_archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_planifiee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )

        # Années école B
        self.annee_b_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        self.annee_b_planifiee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_b.id,
        )

        db.session.add_all([
            self.annee_archivee,
            self.annee_active,
            self.annee_planifiee,
            self.annee_b_active,
            self.annee_b_planifiee,
        ])
        db.session.flush()

        from app.services.structure_annuelle import sauvegarder_structure_annee
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_active.id, [n.id for n in niveaux])
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_planifiee.id, [n.id for n in niveaux])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b_active.id, [n.id for n in niveaux])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b_planifiee.id, [n.id for n in niveaux])
        for n in niveaux:
            db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_archivee.id, niveau_id=n.id, actif=True))
        db.session.commit()

        # Utilisateurs admin
        self.admin_a = Utilisateur(
            nom="AdminA",
            email="admin@alpha.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.admin_b = Utilisateur(
            nom="AdminB",
            email="admin@beta.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.flush()

        # Classe source dans l'année active
        self.cls_source = Classe(
            nom="6e A",
            niveau=self.niv["6E"].nom,
            statut="ouverte",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_active.id,
            niveau_id=self.niv["6E"].id,
        )
        db.session.add(self.cls_source)
        db.session.flush()

        # Élève dans l'année active
        self.eleve = Eleve(
            nom="KONE",
            prenom="Fatou",
            date_naissance=date(2013, 5, 10),
            ecole_id=self.ecole_a.id,
            classe_id=self.cls_source.id,
        )
        db.session.add(self.eleve)
        db.session.flush()

        self.insc_source, _ = creer_inscription_annuelle(
            self.ecole_a.id,
            self.eleve.id,
            self.annee_active.id,
            self.cls_source.id,
            sync_active=True,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            if getattr(user, "ecole_id", None):
                sess["ecole_id"] = user.ecole_id
        return client

    def get_session_year_id(self, client):
        with client.session_transaction() as sess:
            return (sess.get("annee_consultee") or {}).get(str(self.ecole_a.id))

    # -------------------------------------------------------------
    # 1. POST /annees/<planifiee>/consulter -> session change
    # -------------------------------------------------------------
    def test_01_post_annees_consulter_change_session(self):
        client = self.login_as(self.admin_a)
        resp = client.post(f"/annees/{self.annee_planifiee.id}/consulter")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.get_session_year_id(client), self.annee_planifiee.id)

    # -------------------------------------------------------------
    # 2. GET /classes -> utilise ensuite cette année
    # -------------------------------------------------------------
    def test_02_get_classes_utilise_annee_consultee(self):
        client = self.login_as(self.admin_a)
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")

        # Créer une classe dans l'année planifiée
        creer_classe_depuis_niveau(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id,
            niveau_id=self.niv["6E"].id,
            section="B",
        )
        db.session.commit()

        resp = client.get("/classes")
        self.assertEqual(resp.status_code, 200)
        # La classe créée dans l'année planifiée est bien affichée
        self.assertIn(b"6e B", resp.data)
        # L'année consultée est bien 2026-2027
        self.assertIn(self.annee_planifiee.nom.encode("utf-8"), resp.data)

    # -------------------------------------------------------------
    # 3. GET /classes/add -> utilise la même année
    # -------------------------------------------------------------
    def test_03_get_classes_add_utilise_annee_consultee(self):
        client = self.login_as(self.admin_a)
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")

        resp = client.get("/classes/add")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.annee_planifiee.nom.encode("utf-8"), resp.data)

    # -------------------------------------------------------------
    # 4. POST /classes/add -> crée la classe dans cette année
    # -------------------------------------------------------------
    def test_04_post_classes_add_cree_classe_dans_annee_consultee(self):
        client = self.login_as(self.admin_a)
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")

        resp = client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "A",
                "capacite": 35,
                "professeur_principal_id": 0,
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        classe_creee = Classe.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id,
            nom="6e A",
        ).first()
        self.assertIsNotNone(classe_creee)
        self.assertEqual(classe_creee.annee_scolaire_id, self.annee_planifiee.id)

    # -------------------------------------------------------------
    # 5. POST /classes/add -> NE modifie pas annee_consultee
    # -------------------------------------------------------------
    def test_05_post_classes_add_ne_modifie_pas_session(self):
        client = self.login_as(self.admin_a)
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")
        session_before = self.get_session_year_id(client)

        client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "A",
                "capacite": 35,
                "professeur_principal_id": 0,
            },
        )
        session_after = self.get_session_year_id(client)
        # La session reste strictement identique à celle établie par /annees
        self.assertEqual(session_before, session_after)
        self.assertEqual(session_after, self.annee_planifiee.id)

    # -------------------------------------------------------------
    # 6. annee_id=<autre> -> ne change pas le contexte
    # -------------------------------------------------------------
    def test_06_query_param_annee_id_ne_change_pas_le_contexte(self):
        client = self.login_as(self.admin_a)
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")

        # Requêtes avec un query param parasite annee_id=...
        resp_classes = client.get(f"/classesannee_id={self.annee_active.id}")
        self.assertEqual(resp_classes.status_code, 200)
        self.assertEqual(self.get_session_year_id(client), self.annee_planifiee.id)

        resp_add = client.get(f"/classes/addannee_id={self.annee_archivee.id}")
        self.assertEqual(resp_add.status_code, 200)
        # Le contexte affiché reste l'année consultée en session
        self.assertIn(self.annee_planifiee.nom.encode("utf-8"), resp_add.data)
        self.assertEqual(self.get_session_year_id(client), self.annee_planifiee.id)

    # -------------------------------------------------------------
    # 7. Aucune année en session -> fallback sur année active
    # -------------------------------------------------------------
    def test_07_aucune_annee_en_session_fallback_active(self):
        client = self.login_as(self.admin_a)
        # Pas de consultation préalable : la session est vide
        self.assertIsNone(self.get_session_year_id(client))

        # get_annee_consultee retourne l'année active par défaut
        consultee = get_annee_consultee(self.ecole_a.id)
        self.assertIsNotNone(consultee)
        self.assertEqual(consultee.id, self.annee_active.id)

        # GET /classes/add utilise l'année active
        resp = client.get("/classes/add")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.annee_active.nom.encode("utf-8"), resp.data)

    # -------------------------------------------------------------
    # 8. Ne jamais fallback sur "dernière non archivée"
    # -------------------------------------------------------------
    def test_08_ne_jamais_fallback_sur_derniere_non_archivee(self):
        client = self.login_as(self.admin_a)
        # 1. Sans session, la cible est l'année ACTIVE (2025-2026),
        # et JAMAIS la plus récente non archivée (2026-2027 planifiée)
        self.assertIsNone(self.get_session_year_id(client))
        consultee = get_annee_consultee(self.ecole_a.id)
        self.assertEqual(consultee.id, self.annee_active.id)
        self.assertNotEqual(consultee.id, self.annee_planifiee.id)

        resp = client.get("/classes/add")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.annee_active.nom.encode("utf-8"), resp.data)
        self.assertNotIn(self.annee_planifiee.nom.encode("utf-8"), resp.data)

        # 2. Si aucune année active n'existe et aucune session n'est définie :
        # get_annee_consultee DOIT retourner None (ne jamais fallback silencieusement sur planifiee)
        self.annee_active.statut = "archivee"
        db.session.commit()

        self.assertIsNone(get_annee_consultee(self.ecole_a.id))
        # /classes/add refuse et redirige vers gestion_annees
        resp_add = client.get("/classes/add")
        self.assertEqual(resp_add.status_code, 302)

    # -------------------------------------------------------------
    # 9. Année archivée reste bloquée (lecture seule)
    # -------------------------------------------------------------
    def test_09_annee_archivee_reste_bloquee(self):
        client = self.login_as(self.admin_a)
        # Consulter l'année archivée
        client.post(f"/annees/{self.annee_archivee.id}/consulter")

        # GET /classes/add est bloqué
        resp_get = client.get("/classes/add")
        self.assertEqual(resp_get.status_code, 302)

        # POST /classes/add est bloqué
        resp_post = client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["6E"].id,
                "section": "Z",
                "capacite": 35,
            },
        )
        self.assertEqual(resp_post.status_code, 302)

        # POST /annees/<archivee>/structure est bloqué
        resp_struct = client.post(
            f"/annees/{self.annee_archivee.id}/structure",
            data={"niveau_ids": [str(self.niv["6E"].id)]},
            follow_redirects=True,
        )
        self.assertIn("archivee", resp_struct.get_data(as_text=True).lower())

    # -------------------------------------------------------------
    # 10. Année planifiée autorisée (structure, classes, passage)
    # -------------------------------------------------------------
    def test_10_annee_planifiee_autorisee(self):
        client = self.login_as(self.admin_a)
        # 1. Consulter l'année planifiée
        client.post(f"/annees/{self.annee_planifiee.id}/consulter")

        # 2. Configurer la structure annuelle
        resp_struct = client.post(
            f"/annees/{self.annee_planifiee.id}/structure",
            data={"niveau_ids": [str(self.niv["6E"].id), str(self.niv["5E"].id)]},
        )
        self.assertEqual(resp_struct.status_code, 302)

        # 3. Créer une classe dans l'année planifiée
        resp_add = client.post(
            "/classes/add",
            data={
                "niveau_id": self.niv["5E"].id,
                "section": "A",
                "capacite": 35,
                "professeur_principal_id": 0,
            },
            follow_redirects=True,
        )
        self.assertEqual(resp_add.status_code, 200)

        classe_5a = Classe.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_planifiee.id,
            nom="5e A",
        ).first()
        self.assertIsNotNone(classe_5a)

        # 4. Passage d'année vers cette classe
        result, error = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_active.id,
            annee_cible_id=self.annee_planifiee.id,
            decision="passage",
            classe_cible_id=classe_5a.id,
        )
        self.assertIsNone(error)
        self.assertTrue(result["ok"])
        db.session.commit()

        # Inscription cible créée
        insc_cible = get_inscription(self.eleve, self.annee_planifiee)
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.classe_id, classe_5a.id)

        # Eleve.classe_id reste inchangé sur l'année active
        db.session.refresh(self.eleve)
        self.assertEqual(self.eleve.classe_id, self.cls_source.id)

    # -------------------------------------------------------------
    # 11. Multi-école toujours protégé (isolation tenant)
    # -------------------------------------------------------------
    def test_11_multi_ecole_toujours_protege(self):
        client_a = self.login_as(self.admin_a)

        # Consulter une année de l'école B refusé
        resp_consul = client_a.post(
            f"/annees/{self.annee_b_planifiee.id}/consulter",
            follow_redirects=True,
        )
        self.assertIn("invalide", resp_consul.get_data(as_text=True).lower())

        # Modifier la structure d'une année de l'école B refusé (404)
        resp_struct = client_a.post(
            f"/annees/{self.annee_b_planifiee.id}/structure",
            data={"niveau_ids": [str(self.niv["6E"].id)]},
        )
        self.assertEqual(resp_struct.status_code, 404)

        # Création de classe pour l'école B refusée
        classe_b, error_b = creer_classe_depuis_niveau(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_b_planifiee.id,
            niveau_id=self.niv["6E"].id,
            section="A",
        )
        self.assertIsNone(classe_b)
        self.assertIn("invalide", error_b.lower())


if __name__ == "__main__":
    unittest.main()
