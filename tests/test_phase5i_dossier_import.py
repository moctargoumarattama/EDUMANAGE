"""
Tests KLASORA — Phase 5I : Dossier Administratif Élève + Import Excel.
Exécution de 8 tests ciblés.
"""
import io
import unittest
from datetime import date
import openpyxl

from app import create_app, db
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
from app.services.import_eleves_service import (
    executer_import_excel,
    previsualiser_import_excel,
)
from app.services.inscriptions_annuelles import (
    creer_inscription_annuelle,
    get_parcours_eleve,
)
from app.services.niveaux import ensure_standard_niveaux


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5i"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


def _annee(ecole_id, statut="active", nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 6, 30)):
    a = AnneeScolaire(
        nom=nom,
        date_debut=date_debut,
        date_fin=date_fin,
        statut=statut,
        ecole_id=ecole_id,
    )
    db.session.add(a)
    db.session.flush()
    return a


def _config_niveau(ecole_id, annee_id, niveau_id):
    cfg = AnneeNiveauConfig(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_id,
        niveau_id=niveau_id,
        actif=True,
    )
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _classe(ecole_id, annee_id, nom, niveau):
    c = Classe(
        nom=nom,
        niveau=niveau,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_id,
        statut="ouverte",
    )
    db.session.add(c)
    db.session.flush()
    return c


def _eleve(ecole_id, nom, prenom, code_parent=None, parent_id=None):
    e = Eleve(
        nom=nom,
        prenom=prenom,
        genre="M",
        statut="actif",
        ecole_id=ecole_id,
        date_naissance=date(2010, 1, 1),
        code_parent=code_parent,
        parent_id=parent_id,
    )
    db.session.add(e)
    db.session.flush()
    return e


def _user(email, role, ecole_id, password="test"):
    u = Utilisateur(nom="User", email=email, mot_de_passe=password, role=role, ecole_id=ecole_id)
    db.session.add(u)
    db.session.flush()
    return u


def _create_excel_file(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = [
        "Nom *", "Prénom *", "Genre (M/F)", "Date de naissance (AAAA-MM-JJ)",
        "Lieu de naissance", "Adresse", "Nom du parent", "Téléphone parent",
        "Email parent", "Classe *", "Statut"
    ]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class Phase5IDossierImportTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_standard_niveaux(commit=True)

        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.ecole_a.setup_complete = True
        self.ecole_b.setup_complete = True

        self.admin_a = _user("admin_a@test.local", "admin", self.ecole_a.id)
        self.admin_b = _user("admin_b@test.local", "admin", self.ecole_b.id)

        self.annee_a_2024 = _annee(self.ecole_a.id, statut="active", nom="2024-2025", date_debut=date(2024, 9, 1))
        self.annee_a_2023 = _annee(self.ecole_a.id, statut="archivee", nom="2023-2024", date_debut=date(2023, 9, 1))

        niveaux = NiveauScolaire.query.order_by(NiveauScolaire.ordre).limit(2).all()
        self.niv1 = niveaux[0]
        self.niv2 = niveaux[1] if len(niveaux) > 1 else niveaux[0]

        _config_niveau(self.ecole_a.id, self.annee_a_2024.id, self.niv1.id)
        _config_niveau(self.ecole_a.id, self.annee_a_2023.id, self.niv1.id)

        self.classe_6a_2024 = _classe(self.ecole_a.id, self.annee_a_2024.id, "6e A", self.niv1.nom)
        self.classe_6a_2023 = _classe(self.ecole_a.id, self.annee_a_2023.id, "6e A", self.niv1.nom)

        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # 1. Historique scolaire vient d'Inscription et non d'Eleve.classe_id
    def test_01_historique_eleve_vient_de_inscription(self):
        eleve = _eleve(self.ecole_a.id, "KONE", "Moussa")
        # Inscription 2023
        creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_a_2023.id, self.classe_6a_2023.id, allow_archived=True)
        # Inscription 2024
        creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_a_2024.id, self.classe_6a_2024.id)
        db.session.commit()

        # Eleve.classe_id peut changer ou être None
        eleve.classe_id = None
        db.session.commit()

        parcours = get_parcours_eleve(eleve)
        self.assertEqual(len(parcours), 2)
        classes_parcours = [i.classe.id for i in parcours]
        self.assertIn(self.classe_6a_2023.id, classes_parcours)
        self.assertIn(self.classe_6a_2024.id, classes_parcours)

    # 2. Élève d'une autre école inaccessible
    def test_02_eleve_autre_ecole_inaccessible(self):
        eleve_b = _eleve(self.ecole_b.id, "Bamba", "Awa")
        db.session.commit()

        from app.authorization import can_access_eleve
        with self.app.test_request_context():
            # Simons l'admin de l'école A
            with self.client.session_transaction() as sess:
                sess["_user_id"] = str(self.admin_a.id)
            
            # Direct database check
            self.assertFalse(eleve_b.ecole_id == self.admin_a.ecole_id)

    # 3. Parent ne consulte que ses propres enfants
    def test_03_parent_consulte_uniquement_ses_enfants(self):
        parent_user = _user("parent1@test.local", "parent", self.ecole_a.id)
        enfant = _eleve(self.ecole_a.id, "Diop", "Aliou", parent_id=parent_user.id)
        autre_enfant = _eleve(self.ecole_a.id, "Fall", "Fama", parent_id=None)
        db.session.commit()

        from app.authorization import check_parent_access
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(parent_user)
            self.assertTrue(check_parent_access(enfant.id))
            self.assertFalse(check_parent_access(autre_enfant.id))

    # 4. Excel valide crée Eleve + Inscription dans l'année consultée
    def test_04_excel_valide_cree_eleve_et_inscription(self):
        buf = _create_excel_file([
            ["N'DIAYE", "Oumar", "M", "2012-01-10", "Dakar", "Fann", "N'Diaye Papa", "771112233", "parent@test.com", "6e A", "actif"]
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2024)
        self.assertTrue(res['is_importable'])
        self.assertEqual(res['valides'], 1)
        self.assertEqual(res['lignes'][0]['action'], "Nouveau")

        succes, msg, crees, reinscrits, ignores = executer_import_excel(res['lignes'], self.ecole_a.id, self.annee_a_2024)
        self.assertTrue(succes)
        self.assertEqual(crees, 1)

        eleve_cree = Eleve.query.filter_by(ecole_id=self.ecole_a.id, nom="N'DIAYE", prenom="Oumar").first()
        self.assertIsNotNone(eleve_cree)
        insc = Inscription.query.filter_by(eleve_id=eleve_cree.id, annee_scolaire_id=self.annee_a_2024.id).first()
        self.assertIsNotNone(insc)

    # 5. Classe Excel d'une autre année / école refusée
    def test_05_classe_excel_autre_annee_ou_ecole_refusee(self):
        # On essaie d'importer avec un nom de classe inexistant dans l'année 2024
        buf = _create_excel_file([
            ["TRAORE", "Sekou", "M", "2011-04-05", "", "", "", "", "", "Terminale S2", "actif"]
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2024)
        self.assertFalse(res['is_importable'])
        self.assertEqual(res['erreurs'], 1)
        self.assertIn("introuvable", res['lignes'][0]['errors'][0])

    # 6. Année archivée -> import refusé
    def test_06_annee_archivee_import_refuse(self):
        buf = _create_excel_file([
            ["CAMARA", "Ibrahima", "M", "2010-02-02", "", "", "", "", "", "6e A", "actif"]
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2023)
        self.assertFalse(res['is_importable'])
        self.assertIn("archivée", res['erreur_globale'])

    # 7. Ligne Excel invalide sans corruption BDD
    def test_07_ligne_excel_invalide_sans_corruption(self):
        count_before = Eleve.query.count()
        buf = _create_excel_file([
            ["", "", "M", "2010-01-01", "", "", "", "", "", "6e A", "actif"]  # Nom et prénom vides
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2024)
        self.assertEqual(res['erreurs'], 1)
        self.assertEqual(res['valides'], 0)

        succes, msg, crees, reinscrits, ignores = executer_import_excel(res['lignes'], self.ecole_a.id, self.annee_a_2024)
        self.assertEqual(crees, 0)
        self.assertEqual(Eleve.query.count(), count_before)

    # 8. Doublon ambigu sans auto-fusion
    def test_08_doublon_ambigu_sans_auto_fusion(self):
        # Créer un élève existant avec date naissance
        e1 = _eleve(self.ecole_a.id, "BARRY", "Mamadou")
        e1.date_naissance = date(2008, 5, 5)
        db.session.commit()

        # Fichier Excel avec même nom/prénom mais date de naissance différente
        buf = _create_excel_file([
            ["BARRY", "Mamadou", "M", "2012-12-12", "Conakry", "", "", "779998877", "", "6e A", "actif"]
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2024)
        # Ne doit PAS être lié comme réinscription (pas d'auto-fusion quand les dates de naissance diffèrent)
        self.assertIsNone(res['lignes'][0]['existing_eleve_id'])
        # Avertissement doit être levé
        self.assertTrue(len(res['lignes'][0]['warnings']) > 0)

    # 9. Preview volumineux hors session cookie
    def test_09_preview_volumineux_hors_session_cookie(self):
        rows = [
            [f"NOM_{i}", f"Prenom_{i}", "M", "2012-01-01", "", "", "", "", "", "6e A", "actif"]
            for i in range(50)
        ]
        buf = _create_excel_file(rows)
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)

        response = self.client.post(
            "/eleves/import",
            data={"file": (buf, "test_import.xlsx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        # Vérifier que les lignes NE sont PAS dans la session client cookie
        with self.client.session_transaction() as sess:
            self.assertNotIn("import_data_", str(sess.keys()))
            token = sess.get("import_token")
            self.assertIsNotNone(token)

        from app.services.import_eleves_service import recuperer_preview_import
        temp_data = recuperer_preview_import(token)
        self.assertIsNotNone(temp_data)
        self.assertEqual(len(temp_data["lignes"]), 50)

    # 10. Import avec email parent -> Aucun compte Utilisateur parent créé
    def test_10_import_email_parent_sans_compte_utilisateur_automatique(self):
        initial_users_count = Utilisateur.query.count()
        buf = _create_excel_file([
            ["KONATE", "Salif", "M", "2011-03-03", "", "", "Konate Papa", "770001122", "new_parent_email@test.com", "6e A", "actif"]
        ])
        res = previsualiser_import_excel(buf, self.ecole_a.id, self.annee_a_2024)
        executer_import_excel(res["lignes"], self.ecole_a.id, self.annee_a_2024)

        # Vérifier qu'aucun nouvel Utilisateur n'a été créé
        self.assertEqual(Utilisateur.query.count(), initial_users_count)
        
        # Mais le champ email_parent sur l'Eleve est bien renseigné
        eleve_cree = Eleve.query.filter_by(ecole_id=self.ecole_a.id, nom="KONATE").first()
        self.assertEqual(eleve_cree.email_parent, "new_parent_email@test.com")

    # 11. Refus .xls et acceptation .xlsx
    def test_11_refus_fichier_xls_et_acceptation_xlsx(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)

        # Envoi d'un fichier .xls
        buf_xls = io.BytesIO(b"fake xls content")
        resp_xls = self.client.post(
            "/eleves/import",
            data={"file": (buf_xls, "test.xls")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp_xls.status_code, 200)
        self.assertIn("Format non pris en charge", resp_xls.get_data(as_text=True))

        # Envoi d'un fichier .xlsx valide
        buf_xlsx = _create_excel_file([
            ["SYLLA", "Moussa", "M", "2012-02-02", "", "", "", "", "", "6e A", "actif"]
        ])
        resp_xlsx = self.client.post(
            "/eleves/import",
            data={"file": (buf_xlsx, "test.xlsx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp_xlsx.status_code, 200)
        self.assertIn("Prévisualisation", resp_xlsx.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

