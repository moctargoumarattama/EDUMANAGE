import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    EcoleNiveauConfig,
    Eleve,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.niveaux import (
    STANDARD_NIVEAUX,
    creer_classe_depuis_niveau,
    ensure_standard_niveaux,
    proposer_nom_classe,
)
from app.services.structure_annuelle import (
    copier_structure_annee,
    get_classes_niveau_annee,
    get_niveaux_annee,
    get_niveaux_candidats_annuels,
    niveau_est_dans_structure,
    peut_retirer_niveau_structure,
    sauvegarder_structure_annee,
    verifier_integrite_classes,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase4AStructureUniqueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Initialiser les niveaux standards dans le catalogue NiveauScolaire
        self.niveaux_standard = ensure_standard_niveaux(commit=True)
        self.niveaux_by_code = {n.code: n for n in self.niveaux_standard}

        # Établissement A et B
        self.ecole_a = Ecole(nom="Groupe Scolaire Excellence", statut="actif")
        self.ecole_b = Ecole(nom="Collège Moderne B", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années pour École A
        self.annee_a1 = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_a2 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        # Année pour École B
        self.annee_b1 = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a1, self.annee_a2, self.annee_b1])
        db.session.flush()

        # Utilisateurs
        self.admin_a = Utilisateur(
            nom="Admin A",
            email="admin.a@test.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_a.id,
        )
        self.admin_b = Utilisateur(
            nom="Admin B",
            email="admin.b@test.local",
            mot_de_passe="pass",
            role="admin",
            ecole_id=self.ecole_b.id,
        )
        self.prof_a = Utilisateur(
            nom="Prof A",
            email="prof.a@test.local",
            mot_de_passe="pass",
            role="professeur",
            ecole_id=self.ecole_a.id,
        )
        self.parent_a = Utilisateur(
            nom="Parent A",
            email="parent.a@test.local",
            mot_de_passe="pass",
            role="parent",
            ecole_id=self.ecole_a.id,
        )
        db.session.add_all([self.admin_a, self.admin_b, self.prof_a, self.parent_a])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _client_as(self, user, annee=None):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["role"] = user.role
            sess["ecole_id"] = user.ecole_id
            if annee:
                sess["annee_consultee"] = {str(user.ecole_id): annee.id}
        return client

    # ---------------------------------------------------------
    # 1. Source Unique des Niveaux par AnneeNiveauConfig
    # ---------------------------------------------------------
    def test_01_get_niveaux_annee_retourne_uniquement_actifs(self):
        n_ci = self.niveaux_by_code["CI"]
        n_cp = self.niveaux_by_code["CP"]
        n_ce1 = self.niveaux_by_code["CE1"]

        # Configurer Année A1 avec CI et CP actifs, CE1 inactif
        db.session.add_all([
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_ci.id, actif=True),
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_cp.id, actif=True),
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_ce1.id, actif=False),
        ])
        db.session.commit()

        niveaux = get_niveaux_annee(self.ecole_a.id, self.annee_a1.id)
        codes = [n.code for n in niveaux]
        self.assertIn("CI", codes)
        self.assertIn("CP", codes)
        self.assertNotIn("CE1", codes)
        self.assertEqual(len(niveaux), 2)

    def test_02_get_niveaux_annee_vide_si_non_configure(self):
        niveaux = get_niveaux_annee(self.ecole_a.id, self.annee_a2.id)
        self.assertEqual(niveaux, [])

    def test_03_isolation_multi_annees_meme_ecole(self):
        # A1 a CI et CP
        n_ci = self.niveaux_by_code["CI"]
        n_cp = self.niveaux_by_code["CP"]
        # A2 a 6e et 5e
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]

        db.session.add_all([
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_ci.id, actif=True),
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_cp.id, actif=True),
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a2.id, niveau_id=n_6e.id, actif=True),
            AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a2.id, niveau_id=n_5e.id, actif=True),
        ])
        db.session.commit()

        niveaux_a1 = [n.code for n in get_niveaux_annee(self.ecole_a.id, self.annee_a1.id)]
        niveaux_a2 = [n.code for n in get_niveaux_annee(self.ecole_a.id, self.annee_a2.id)]

        self.assertEqual(sorted(niveaux_a1), ["CI", "CP"])
        self.assertEqual(sorted(niveaux_a2), ["5E", "6E"])

    def test_04_isolation_multi_ecoles(self):
        n_6e = self.niveaux_by_code["6E"]
        n_term = self.niveaux_by_code["TERMINALE"]

        # Ecole A active 6e
        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_6e.id, actif=True))
        # Ecole B active Terminale
        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b1.id, niveau_id=n_term.id, actif=True))
        db.session.commit()

        niveaux_a = [n.code for n in get_niveaux_annee(self.ecole_a.id, self.annee_a1.id)]
        niveaux_b = [n.code for n in get_niveaux_annee(self.ecole_b.id, self.annee_b1.id)]

        self.assertEqual(niveaux_a, ["6E"])
        self.assertEqual(niveaux_b, ["TERMINALE"])

    # ---------------------------------------------------------
    # 2. niveau_est_dans_structure
    # ---------------------------------------------------------
    def test_05_niveau_est_dans_structure_strict(self):
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]

        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_6e.id, actif=True))
        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a1.id, niveau_id=n_5e.id, actif=False))
        db.session.commit()

        self.assertTrue(niveau_est_dans_structure(self.ecole_a.id, self.annee_a1.id, n_6e.id))
        self.assertFalse(niveau_est_dans_structure(self.ecole_a.id, self.annee_a1.id, n_5e.id))
        self.assertFalse(niveau_est_dans_structure(self.ecole_a.id, self.annee_a1.id, self.niveaux_by_code["4E"].id))

    # ---------------------------------------------------------
    # 3. Création de Classe et Validation Annuelle
    # ---------------------------------------------------------
    def test_06_creation_classe_niveau_dans_structure_succes(self):
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        classe, error = creer_classe_depuis_niveau(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a1.id,
            niveau_id=n_6e.id,
            section="A",
        )
        self.assertIsNone(error)
        self.assertIsNotNone(classe)
        self.assertEqual(classe.nom, "6e A")
        self.assertEqual(classe.niveau_id, n_6e.id)
        self.assertEqual(classe.niveau, "6e")

    def test_07_creation_classe_niveau_hors_structure_bloquee(self):
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        # Seul 6e est configuré dans l'année A1
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        # Tentative de créer une 5e A dans l'année A1
        classe, error = creer_classe_depuis_niveau(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a1.id,
            niveau_id=n_5e.id,
            section="A",
        )
        self.assertIsNone(classe)
        self.assertIsNotNone(error)
        self.assertIn("pas retenu", error)

    def test_08_nommage_section_college_vs_serie_lycee(self):
        n_6e = self.niveaux_by_code["6E"]
        n_2nde = self.niveaux_by_code["2NDE"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id, n_2nde.id])
        db.session.commit()

        # Collège -> 6e A
        nom_college = proposer_nom_classe(n_6e, "A")
        self.assertEqual(nom_college, "6e A")

        # Lycée -> 2nde Serie A
        nom_lycee = proposer_nom_classe(n_2nde, "A")
        self.assertEqual(nom_lycee, "2nde Serie A")

    # ---------------------------------------------------------
    # 4. Protection d'Intégrité : Retrait de Niveau
    # ---------------------------------------------------------
    def test_09_peut_retirer_niveau_structure_sans_classes(self):
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        peut_retirer, raison = peut_retirer_niveau_structure(self.ecole_a.id, self.annee_a1.id, n_6e.id)
        self.assertTrue(peut_retirer)
        self.assertIsNone(raison)

    def test_10_peut_retirer_niveau_structure_bloque_si_classe_existante(self):
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        classe, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_6e.id, section="A")
        db.session.commit()

        peut_retirer, raison = peut_retirer_niveau_structure(self.ecole_a.id, self.annee_a1.id, n_6e.id)
        self.assertFalse(peut_retirer)
        self.assertIn("possède déjà des classes", raison)

    def test_11_sauvegarder_structure_annee_bloque_retrait_niveau_utilise(self):
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id, n_5e.id])
        db.session.commit()

        # Créer une classe pour 6e
        creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_6e.id, section="A")
        db.session.commit()

        # Tentative de sauvegarder la structure en décochant 6e (en gardant seulement 5e)
        res, error = sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_5e.id])
        self.assertIsNone(res)
        self.assertIsNotNone(error)
        self.assertIn("ne peut pas être retiré", error)

    # ---------------------------------------------------------
    # 5. Route /classes/add et protection contre POST falsifié
    # ---------------------------------------------------------
    def test_12_route_ajouter_classe_get_propose_niveaux_annee_active(self):
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get("/classes/add")
        self.assertEqual(resp.status_code, 200)
        # Vérifie que 6e est présent dans le formulaire
        self.assertIn(b"6e", resp.data)

    def test_13_route_ajouter_classe_post_falsifie_refuse(self):
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        # Seul 6e est dans la structure annuelle
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)
        # Requête POST forgée avec niveau_id=n_5e.id
        resp = client.post("/classes/add", data={
            "niveau_id": n_5e.id,
            "section": "A",
            "capacite": 30,
            "professeur_principal_id": 0,
        }, follow_redirects=True)

        self.assertEqual(resp.status_code, 200)
        # Doit afficher une erreur ou refuser
        # Vérifier qu'aucune classe de 5e n'a été créée
        classe_5e = Classe.query.filter_by(ecole_id=self.ecole_a.id, niveau_id=n_5e.id).first()
        self.assertIsNone(classe_5e)

    # ---------------------------------------------------------
    # 6. Route /api/niveaux
    # ---------------------------------------------------------
    def test_14_api_niveaux_respecte_annee_consultee_et_ecole(self):
        n_ci = self.niveaux_by_code["CI"]
        n_cp = self.niveaux_by_code["CP"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_ci.id, n_cp.id])
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get("/api/niveaux")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        noms = [item["nom"] for item in data]
        self.assertIn("CI", noms)
        self.assertIn("CP", noms)
        self.assertEqual(len(data), 2)

    def test_15_api_niveaux_avec_param_annee_id(self):
        n_ci = self.niveaux_by_code["CI"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_ci.id])
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a2.id, [n_6e.id])
        db.session.commit()

        # Utilisateur a pour année consultée A1, mais demande explicitement annee_id=A2
        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get(f"/api/niveaux?annee_id={self.annee_a2.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["nom"], "6e")

    def test_16_api_niveaux_isolation_ecole_interdite(self):
        # Admin A tente d'accéder à l'année de l'École B
        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get(f"/api/niveaux?annee_id={self.annee_b1.id}")
        self.assertEqual(resp.status_code, 403)

    # ---------------------------------------------------------
    # 7. Copie de Structure Annuelle
    # ---------------------------------------------------------
    def test_17_copier_structure_annee(self):
        n_ci = self.niveaux_by_code["CI"]
        n_cp = self.niveaux_by_code["CP"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_ci.id, n_cp.id])
        db.session.commit()

        # Copier de A1 vers A2
        res, error = copier_structure_annee(self.ecole_a.id, self.annee_a1.id, self.annee_a2.id)
        self.assertIsNone(error)
        self.assertIsNotNone(res)

        niveaux_a2 = [n.code for n in get_niveaux_annee(self.ecole_a.id, self.annee_a2.id)]
        self.assertEqual(sorted(niveaux_a2), ["CI", "CP"])

        # Vérifier que modifier A2 n'altère pas A1
        n_ce1 = self.niveaux_by_code["CE1"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a2.id, [n_ci.id, n_cp.id, n_ce1.id])
        db.session.commit()

        self.assertEqual(len(get_niveaux_annee(self.ecole_a.id, self.annee_a1.id)), 2)
        self.assertEqual(len(get_niveaux_annee(self.ecole_a.id, self.annee_a2.id)), 3)

    # ---------------------------------------------------------
    # 8. Filtres par Niveau dans /classes
    # ---------------------------------------------------------
    def test_18_liste_classes_filtre_par_niveau(self):
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id, n_5e.id])
        db.session.commit()

        c6a, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_6e.id, section="A")
        c5a, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_5e.id, section="A")
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)

        # Filtre par nom de niveau '6e'
        resp = client.get("/classes?niveau=6e")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"6e A", resp.data)
        self.assertNotIn(b"5e A", resp.data)

        # Filtre par niveau_id
        resp2 = client.get(f"/classes?niveau={n_5e.id}")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"5e A", resp2.data)
        self.assertNotIn(b"6e A", resp2.data)

    # ---------------------------------------------------------
    # 9. Contrôle d'Intégrité
    # ---------------------------------------------------------
    def test_19_verifier_integrite_classes(self):
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        c, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_6e.id, section="A")
        db.session.commit()

        # Toutes les classes sont dans la structure
        hors = verifier_integrite_classes(self.ecole_a.id)
        self.assertEqual(len(hors), 0)

        # Si on crée artificiellement une classe orpheline sans config
        c_orpheline = Classe(
            nom="Orpheline",
            niveau="Inconnu",
            niveau_id=9999,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a1.id,
        )
        db.session.add(c_orpheline)
        db.session.commit()

        hors2 = verifier_integrite_classes(self.ecole_a.id)
        self.assertEqual(len(hors2), 1)
        self.assertEqual(hors2[0].id, c_orpheline.id)

    # ---------------------------------------------------------
    # 10. Cas de Validation Base Vierge et Rôles (A à H)
    # ---------------------------------------------------------
    def test_20_cas_a_ecole_niveau_config_vide_structure_fonctionne(self):
        """Cas A: EcoleNiveauConfig vide -> Structure fonctionne."""
        EcoleNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id).delete()
        db.session.commit()

        candidats = get_niveaux_candidats_annuels(self.ecole_a.id)
        self.assertEqual(len(candidats), 13)

        n_6e = self.niveaux_by_code["6E"]
        res, error = sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        self.assertIsNone(error)
        self.assertEqual(res["selected_ids"], {n_6e.id})

    def test_21_cas_b_niveau_scolaire_contient_tous_niveaux_structure_les_propose(self):
        """Cas B: NiveauScolaire contient tous les niveaux -> Structure les propose."""
        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get(f"/annees/{self.annee_a1.id}/structure")
        self.assertEqual(resp.status_code, 200)
        for code, nom, _cycle in STANDARD_NIVEAUX:
            self.assertIn(nom.encode("utf-8"), resp.data)

    def test_22_cas_c_admin_selectionne_seulement_6e_et_5e(self):
        """Cas C: Admin sélectionne seulement 6e et 5e -> AnneeNiveauConfig contient seulement 6e et 5e."""
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id, n_5e.id])
        db.session.commit()

        configs = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a1.id,
        ).all()
        self.assertEqual(len(configs), 2)
        niveau_ids = {c.niveau_id for c in configs}
        self.assertEqual(niveau_ids, {n_6e.id, n_5e.id})

    def test_23_cas_d_classes_add_propose_seulement_6e_et_5e(self):
        """Cas D: /classes/add -> seulement 6e et 5e."""
        n_6e = self.niveaux_by_code["6E"]
        n_5e = self.niveaux_by_code["5E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id, n_5e.id])
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get("/classes/add")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"6e", resp.data)
        self.assertIn(b"5e", resp.data)
        self.assertNotIn(b'value="' + str(self.niveaux_by_code["TERMINALE"].id).encode() + b'"', resp.data)

    def test_24_cas_e_niveau_hors_structure_creation_refusee(self):
        """Cas E: niveau hors structure -> création refusée."""
        n_6e = self.niveaux_by_code["6E"]
        n_term = self.niveaux_by_code["TERMINALE"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.post("/classes/add", data={
            "niveau_id": n_term.id,
            "section": "A",
            "capacite": 30,
        }, follow_redirects=True)
        self.assertTrue(b"Not a valid choice" in resp.data or b"pas retenu" in resp.data)
        created = Classe.query.filter_by(ecole_id=self.ecole_a.id, niveau_id=n_term.id).first()
        self.assertIsNone(created)

    def test_25_cas_f_ecole_bien_configuree_pas_de_boucle_onboarding(self):
        """Cas F: école correctement configurée -> pas de boucle onboarding."""
        from app.utils import get_school_setup_state
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        db.session.commit()

        state = get_school_setup_state(self.ecole_a.id, force_refresh=True)
        self.assertTrue(state["setup_complete"])
        self.assertEqual(state["current_step"], "complete")

        client = self._client_as(self.admin_a, self.annee_a1)
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Tableau de bord", resp.data)
        self.assertNotIn(b"onboarding-title", resp.data)

    def test_26_cas_g_professeur_jamais_redirige_onboarding(self):
        """Cas G: professeur -> jamais redirigé onboarding."""
        client = self._client_as(self.prof_a, self.annee_a1)
        resp_home = client.get("/")
        # Redirection vers le dashboard professeur, JAMAIS vers onboarding
        self.assertEqual(resp_home.status_code, 302)
        self.assertIn("/professeur/dashboard", resp_home.headers.get("Location", ""))

        # Accès direct à /onboarding interdit au professeur (403)
        resp_onb = client.get("/onboarding")
        self.assertEqual(resp_onb.status_code, 403)

    def test_27_cas_h_parent_jamais_redirige_onboarding(self):
        """Cas H: parent -> jamais redirigé onboarding."""
        client = self._client_as(self.parent_a, self.annee_a1)
        resp_home = client.get("/")
        # Redirection vers le dashboard parent, JAMAIS vers onboarding
        self.assertEqual(resp_home.status_code, 302)
        self.assertIn("/parent/dashboard", resp_home.headers.get("Location", ""))

        # Accès direct à /onboarding interdit au parent (403)
        resp_onb = client.get("/onboarding")
        self.assertEqual(resp_onb.status_code, 403)

    def test_28_admin_ecole_configuree_assigner_classes_pas_onboarding(self):
        """Non-régression : un admin d'une école configurée accède à /professeurs et /professeur/<id>/assigner_classes sans être redirigé vers /onboarding."""
        from app.models import Professeur

        # Configurer la structure annuelle pour ecole_a / annee_a1
        n_6e = self.niveaux_by_code["6E"]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a1.id, [n_6e.id])
        c6a, _ = creer_classe_depuis_niveau(self.ecole_a.id, self.annee_a1.id, n_6e.id, section="A")

        # Créer l'entité Professeur liée à self.prof_a
        prof = Professeur(
            nom="Diallo",
            prenom="Amadou",
            email="prof.a@test.local",
            specialite="Maths",
            utilisateur_id=self.prof_a.id,
            ecole_id=self.ecole_a.id,
        )
        db.session.add(prof)
        db.session.commit()

        client = self._client_as(self.admin_a, self.annee_a1)

        # 1. Vérifier GET /professeurs
        resp_profs = client.get("/professeurs")
        self.assertEqual(resp_profs.status_code, 200)
        self.assertNotIn(b"/onboarding", resp_profs.headers.get("Location", b""))

        # 2. Vérifier GET /professeur/<id>/assigner_classes
        resp_assign = client.get(f"/professeur/{prof.id}/assigner_classes")
        self.assertEqual(resp_assign.status_code, 200)
        self.assertNotIn(b"/onboarding", resp_assign.headers.get("Location", b""))
        self.assertIn(b"6e A", resp_assign.data)

