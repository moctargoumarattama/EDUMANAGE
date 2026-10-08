"""Matricules permanents et dédoublonnage, sur une base dédiée en mémoire."""

import io
import unittest
from datetime import date, datetime
from unittest.mock import patch

import openpyxl
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    CertificatAdministratif,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    MatriculeSequence,
    Utilisateur,
)
from app.services.eleves import rechercher_eleves
from app.services.import_eleves_service import (
    executer_import_excel,
    previsualiser_import_excel,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.matricule_service import generer_prochain_matricule
from app.services.bulletin_verification import generer_token_eleve


class MatriculeTestConfig(Config):
    TESTING = True
    SECRET_KEY = "matricules-tests-only"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class MatriculePermanentAntiDoublonTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(MatriculeTestConfig)
        self.context = self.app.app_context()
        self.context.push()
        # Arrêter avant toute écriture si une configuration change cette isolation.
        self.assertEqual(db.engine.url.database, ":memory:")
        db.create_all()

        self.ecole = Ecole(nom="Ecole matricules A", onboarding_complete=True)
        self.autre_ecole = Ecole(nom="Ecole matricules B", onboarding_complete=True)
        db.session.add_all([self.ecole, self.autre_ecole])
        db.session.flush()
        self.annee = self._annee(self.ecole, 2026, "active")
        self.annee_suivante = self._annee(self.ecole, 2027, "planifiee")
        self.autre_annee = self._annee(self.autre_ecole, 2026, "active")
        self.classe = self._classe(self.ecole, self.annee)
        self.classe_suivante = self._classe(self.ecole, self.annee_suivante)
        self.autre_classe = self._classe(self.autre_ecole, self.autre_annee)
        self.admin = Utilisateur(
            nom="Administration test",
            email="matricules-admin@test.local",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.admin.set_mot_de_passe("admin-tests-only")
        self.parent = Utilisateur(
            nom="Tuteur test",
            telephone="90123456",
            role="parent",
            ecole_id=self.ecole.id,
        )
        self.parent.set_mot_de_passe("58310427")
        db.session.add_all([self.admin, self.parent])
        db.session.commit()
        self.client = self.app.test_client()
        self._login(self.annee)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _annee(self, ecole, debut, statut):
        annee = AnneeScolaire(
            nom=f"{debut}-{debut + 1}",
            date_debut=date(debut, 9, 1),
            date_fin=date(debut + 1, 7, 31),
            statut=statut,
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.flush()
        return annee

    def _classe(self, ecole, annee):
        classe = Classe(
            nom="6e A",
            niveau="6e",
            statut="ouverte",
            ecole_id=ecole.id,
            annee_scolaire_id=annee.id,
        )
        db.session.add(classe)
        db.session.flush()
        return classe

    def _login(self, annee):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
            session["ecole_id"] = self.ecole.id
            session["annee_consultee"] = {str(self.ecole.id): annee.id}

    def _eleve(self, nom="Sow", prenom="Awa", **overrides):
        values = {
            "nom": nom,
            "prenom": prenom,
            "date_naissance": date(2014, 1, 1),
            "ecole_id": self.ecole.id,
            "annee_premiere_ecole": 2026,
            "date_inscription": datetime(2026, 9, 2),
        }
        values.update(overrides)
        eleve = Eleve(**values)
        db.session.add(eleve)
        db.session.commit()
        return eleve

    def _inscrire(self, eleve, annee=None, classe=None):
        inscription, error = creer_inscription_annuelle(
            ecole_id=eleve.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=(annee or self.annee).id,
            classe_id=(classe or self.classe).id,
        )
        self.assertIsNone(error)
        db.session.commit()
        return inscription

    def _payload(self, classe=None, **overrides):
        payload = {
            "nom": "Sow",
            "prenom": "Awa",
            "genre": "F",
            "date_naissance": "2014-01-01",
            "lieu_naissance": "Niamey",
            "adresse": "Niamey",
            "classe_id": str((classe or self.classe).id),
            "frais_annuels": "150000",
            "parent_id": str(self.parent.id),
            "code_parent": "",
        }
        payload.update(overrides)
        return payload

    def _excel(self, rows):
        workbook = openpyxl.Workbook()
        workbook.active.append([
            "Nom *", "Prénom *", "Genre (M/F)", "Date de naissance",
            "Lieu de naissance", "Adresse", "Nom du parent",
            "Téléphone parent", "Email parent", "Classe *", "Statut",
        ])
        for row in rows:
            workbook.active.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return stream

    def _excel_row(self, nom="Sow", prenom="Awa", naissance="2014-01-01"):
        return [nom, prenom, "F", naissance, "Niamey", "", "", "", "", "6e A", "actif"]

    def _preview(self, rows, annee=None):
        return previsualiser_import_excel(
            self._excel(rows), self.ecole.id, annee or self.annee
        )

    def _execute(self, preview, annee=None):
        return executer_import_excel(preview["lignes"], self.ecole.id, annee or self.annee)

    def _certificat(self, eleve):
        cert = CertificatAdministratif(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            type_certificat="scolarite",
            reference="CS-2026-0001",
            annee_scolaire="2026-2027",
            classe_nom="6e A",
            signataire_nom="Administration test",
            code_verification="matricule-test-public-verification",
        )
        db.session.add(cert)
        db.session.commit()
        return cert

    def test_numero_auto_sequentiel_par_ecole_et_annee_entree(self):
        premier = self._eleve(code_parent="58310427")
        second = self._eleve(nom="Diallo", prenom="Amadou")
        nouvelle_annee = self._eleve(nom="Issa", annee_premiere_ecole=2027)
        autre_ecole = self._eleve(nom="Moussa", ecole_id=self.autre_ecole.id)

        self.assertEqual(premier.matricule, "26-0001")
        self.assertEqual(second.matricule, "26-0002")
        self.assertEqual(nouvelle_annee.matricule, "27-0001")
        self.assertEqual(autre_ecole.matricule, "26-0001")
        self.assertNotEqual(premier.matricule, premier.code_parent)

    def test_annee_entree_prioritaire_sur_date_creation(self):
        eleve = self._eleve(
            annee_premiere_ecole=2024,
            date_inscription=datetime(2030, 1, 1),
        )
        self.assertEqual(eleve.matricule, "24-0001")

    def test_reservations_sans_insertion_uniques_et_annulees_par_rollback(self):
        self.assertEqual(generer_prochain_matricule(self.ecole.id, 2024), "24-0001")
        self.assertEqual(generer_prochain_matricule(self.ecole.id, 2024), "24-0002")
        db.session.rollback()
        self.assertEqual(generer_prochain_matricule(self.ecole.id, 2024), "24-0001")
        db.session.commit()
        self.assertEqual(generer_prochain_matricule(self.ecole.id, 2024), "24-0002")

    def test_limite_9999_refuse_creation_sans_alterer_compteur(self):
        db.session.add(MatriculeSequence(
            ecole_id=self.ecole.id, prefixe="26", dernier_numero=9999
        ))
        db.session.commit()
        with self.assertRaises(ValueError):
            self._eleve()
        db.session.rollback()
        self.assertEqual(Eleve.query.count(), 0)
        sequence = db.session.get(MatriculeSequence, (self.ecole.id, "26"))
        self.assertEqual(sequence.dernier_numero, 9999)
        self.assertEqual(generer_prochain_matricule(self.ecole.id, 2027), "27-0001")

    def test_ancien_eleve_sans_matricule_en_recoit_un_lors_mise_a_jour(self):
        ancien = db.session.execute(Eleve.__table__.insert().values(
            nom="Ancien", prenom="Sans numéro", date_naissance=date(2014, 1, 1),
            ecole_id=self.ecole.id, matricule=None,
            annee_premiere_ecole=2026, date_inscription=datetime(2026, 9, 2),
        ))
        db.session.commit()
        eleve = db.session.get(Eleve, ancien.inserted_primary_key[0])
        self.assertIsNone(eleve.matricule)
        eleve.adresse = "Adresse corrigée"
        db.session.commit()
        self.assertRegex(eleve.matricule, r"^26-[0-9]{4}$")
        self.assertNotEqual(eleve.matricule[-4:], "0000")

    def test_matricule_malforme_ne_peut_pas_etre_insere(self):
        for invalide in ("26-0000", "58310427", "26-12345"):
            with self.assertRaises(ValueError):
                self._eleve(matricule=invalide)
            db.session.rollback()
        self.assertEqual(Eleve.query.count(), 0)

    def test_date_creation_utilisee_si_annee_entree_absente(self):
        eleve = self._eleve(
            annee_premiere_ecole=None,
            date_inscription=datetime(2023, 3, 1),
        )
        self.assertEqual(eleve.matricule, "23-0001")

    def test_unicite_matricule_protegee_par_base(self):
        premier = self._eleve()
        second = self._eleve(nom="Diallo")
        original = second.matricule
        with self.assertRaises(IntegrityError):
            db.session.execute(
                update(Eleve)
                .where(Eleve.id == second.id)
                .values(matricule=premier.matricule)
            )
            db.session.commit()
        db.session.rollback()
        self.assertEqual(second.matricule, original)

    def test_matricule_attribue_ne_peut_plus_etre_modifie(self):
        eleve = self._eleve()
        original = eleve.matricule
        with self.assertRaises((ValueError, IntegrityError)):
            eleve.matricule = "26-9876"
            db.session.commit()
        db.session.rollback()
        self.assertEqual(eleve.matricule, original)

    def test_permanence_protege_aussi_attribut_orm_expire(self):
        eleve = self._eleve()
        original = eleve.matricule
        db.session.expire(eleve, ["matricule"])
        with self.assertRaises((ValueError, IntegrityError)):
            eleve.matricule = "26-9876"
            db.session.commit()
        db.session.rollback()
        self.assertEqual(eleve.matricule, original)

    def test_reinscription_annuelle_conserve_identifiant(self):
        eleve = self._eleve()
        matricule = eleve.matricule
        self._inscrire(eleve)
        self._inscrire(eleve, self.annee_suivante, self.classe_suivante)
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.filter_by(eleve_id=eleve.id).count(), 2)
        self.assertEqual(eleve.matricule, matricule)

    def test_reset_pin_et_mot_de_passe_ne_changent_pas_matricule(self):
        eleve = self._eleve(code_parent="58310427", parent_id=self.parent.id)
        matricule = eleve.matricule
        with patch.object(Eleve, "generer_code_parent", return_value="24681357"):
            response = self.client.post(f"/admin/eleve/{eleve.id}/regenerer-code")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        db.session.refresh(eleve)
        self.assertEqual(eleve.code_parent, "24681357")
        self.assertEqual(eleve.matricule, matricule)

        with patch("app.routes.utilisateurs.generate_access_code", return_value="13572468"):
            response = self.client.post(f"/admin/utilisateur/{self.parent.id}/reset-password")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        db.session.refresh(self.parent)
        db.session.refresh(eleve)
        self.assertTrue(self.parent.check_mot_de_passe("13572468"))
        self.assertEqual(eleve.matricule, matricule)

    def test_saisie_manuelle_doublon_meme_annee_sans_nouvelle_fiche(self):
        eleve = self._eleve()
        self._inscrire(eleve)
        response = self.client.post(
            "/ajouter_eleve", data=self._payload(nom=" SOW ", prenom=" awa ")
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.count(), 1)

    def test_saisie_manuelle_ancien_eleve_oriente_vers_reinscription_du_dossier(self):
        eleve = self._eleve()
        matricule = eleve.matricule
        self._inscrire(eleve)
        self._login(self.annee_suivante)
        response = self.client.post(
            "/ajouter_eleve", data=self._payload(classe=self.classe_suivante)
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.filter_by(eleve_id=eleve.id).count(), 1)
        with self.client.session_transaction() as session:
            messages = " ".join(message for _, message in session.get("_flashes", []))
        self.assertIn(matricule, messages)
        self.assertIn("réinscription", messages.lower())
        response = self.client.post(
            f"/annees/{self.annee_suivante.id}/reinscrire_eleve",
            data={"eleve_id": eleve.id, "classe_cible_id": self.classe_suivante.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.filter_by(eleve_id=eleve.id).count(), 2)
        self.assertEqual(eleve.matricule, matricule)

    def test_edition_json_refuse_identite_deja_presente(self):
        existant = self._eleve()
        autre = self._eleve(nom="Diallo", prenom="Amadou")
        self._inscrire(autre)
        matricule = autre.matricule
        response = self.client.post(
            f"/api/eleves/{autre.id}/modifier",
            json=self._payload(nom=" SOW ", prenom=" awa "),
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["success"])
        self.assertIn(existant.matricule, response.get_json()["error"])
        db.session.refresh(autre)
        self.assertEqual((autre.nom, autre.prenom, autre.matricule), ("Diallo", "Amadou", matricule))
        self.assertEqual(Eleve.query.count(), 2)

    def test_edition_formulaire_refuse_identite_deja_presente(self):
        self._eleve()
        autre = self._eleve(nom="Diallo", prenom="Amadou")
        self._inscrire(autre)
        matricule = autre.matricule
        response = self.client.post(
            f"/eleve/{autre.id}/modifier", data=self._payload()
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(autre)
        self.assertEqual((autre.nom, autre.prenom, autre.matricule), ("Diallo", "Amadou", matricule))
        self.assertEqual(Eleve.query.count(), 2)

    def test_edition_propre_conserve_matricule_et_ignore_matricule_soumis(self):
        eleve = self._eleve(parent_id=self.parent.id)
        self._inscrire(eleve)
        matricule = eleve.matricule
        response = self.client.post(
            f"/api/eleves/{eleve.id}/modifier",
            json=self._payload(adresse="Nouvelle adresse", matricule="26-9999"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        db.session.refresh(eleve)
        self.assertEqual(eleve.adresse, "Nouvelle adresse")
        self.assertEqual(eleve.matricule, matricule)

    def test_homonyme_date_naissance_distincte_reste_autre_eleve(self):
        self._eleve()
        response = self.client.post(
            "/ajouter_eleve", data=self._payload(date_naissance="2014-02-01")
        )
        self.assertEqual(response.status_code, 302)
        eleves = Eleve.query.order_by(Eleve.id).all()
        self.assertEqual(len(eleves), 2)
        self.assertNotEqual(eleves[0].matricule, eleves[1].matricule)

    def test_excel_nouveaux_eleves_recoivent_matricules_annee_consultee(self):
        preview = self._preview([
            self._excel_row(), self._excel_row(nom="Diallo", prenom="Amadou")
        ], self.annee_suivante)
        self.assertTrue(preview["is_importable"])
        success, message, created, _, _ = self._execute(preview, self.annee_suivante)
        self.assertTrue(success, message)
        self.assertEqual(created, 2)
        self.assertEqual(
            [e.matricule for e in Eleve.query.order_by(Eleve.id)],
            ["27-0001", "27-0002"],
        )
        self.assertEqual(Inscription.query.count(), 2)

    def test_excel_identite_repetee_ne_cree_pas_deux_eleves(self):
        preview = self._preview([
            self._excel_row(), self._excel_row(nom=" SOW ", prenom=" awa ")
        ])
        self.assertEqual(preview["valides"], 1)
        self.assertEqual(preview["erreurs"], 1)
        success, message, created, _, ignored = self._execute(preview)
        self.assertTrue(success, message)
        self.assertEqual(created, 1)
        self.assertGreaterEqual(ignored, 1)
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.count(), 1)

    def test_excel_date_naissance_absente_bloquee_avant_execution(self):
        preview = self._preview([self._excel_row(naissance="")])
        self.assertFalse(preview["is_importable"])
        self.assertEqual(preview["erreurs"], 1)
        self.assertEqual(Eleve.query.count(), 0)

    def test_excel_reinscrit_eleve_permanent_sans_regenerer_matricule(self):
        eleve = self._eleve()
        matricule = eleve.matricule
        self._inscrire(eleve)
        preview = self._preview([self._excel_row()], self.annee_suivante)
        self.assertEqual(preview["lignes"][0]["existing_eleve_id"], eleve.id)
        success, message, created, reinscrits, _ = self._execute(preview, self.annee_suivante)
        self.assertTrue(success, message)
        self.assertEqual((created, reinscrits), (0, 1))
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.filter_by(eleve_id=eleve.id).count(), 2)
        self.assertEqual(eleve.matricule, matricule)

    def test_excel_preview_perime_reverifie_identite_et_reutilise_fiche(self):
        preview = self._preview([self._excel_row()])
        self.assertIsNone(preview["lignes"][0]["existing_eleve_id"])
        apparu_entretemps = self._eleve()
        matricule = apparu_entretemps.matricule
        success, message, created, reinscrits, _ = self._execute(preview)
        self.assertTrue(success, message)
        self.assertEqual((created, reinscrits), (0, 1))
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.one().eleve_id, apparu_entretemps.id)
        self.assertEqual(apparu_entretemps.matricule, matricule)

    def test_excel_preview_perime_ignore_inscription_creee_entretemps(self):
        preview = self._preview([self._excel_row()])
        eleve = self._eleve()
        self._inscrire(eleve)
        success, message, created, reinscrits, ignored = self._execute(preview)
        self.assertTrue(success, message)
        self.assertEqual((created, reinscrits, ignored), (0, 0, 1))
        self.assertEqual(Eleve.query.count(), 1)
        self.assertEqual(Inscription.query.count(), 1)

    def test_recherche_matricule_entier_suffixe_isole_ecole_et_exclut_pin(self):
        eleve = self._eleve(code_parent="58310427")
        self._inscrire(eleve)
        autre = self._eleve(nom="Autre", ecole_id=self.autre_ecole.id)
        self._inscrire(autre, self.autre_annee, self.autre_classe)
        for search in (eleve.matricule, eleve.matricule.split("-")[1]):
            results = rechercher_eleves(self.ecole.id, self.annee.id, search=search)
            self.assertEqual([r.id for r in results], [eleve.id])
        self.assertEqual(
            rechercher_eleves(self.ecole.id, self.annee.id, search=eleve.code_parent), []
        )

    def test_certificat_imprime_et_public_affichent_matricule_sans_secrets(self):
        eleve = self._eleve(code_parent="58310427", parent_id=self.parent.id)
        cert = self._certificat(eleve)
        printed = self.client.get(f"/certificats/imprimer/{cert.id}")
        self.assertEqual(printed.status_code, 200)
        public = self.app.test_client().get(f"/certificats/verifier/{cert.code_verification}")
        self.assertEqual(public.status_code, 200)
        for response in (printed, public):
            html = response.get_data(as_text=True)
            self.assertIn(eleve.matricule, html)
            self.assertNotIn(eleve.code_parent, html)
            self.assertNotIn(self.parent.mot_de_passe, html)
            self.assertNotIn(self.admin.mot_de_passe, html)

    def test_api_fiche_liste_classe_qr_exposent_matricule_sans_secrets(self):
        eleve = self._eleve(code_parent="58310427", parent_id=self.parent.id)
        self._inscrire(eleve)
        for path in (
            f"/api/eleves/{eleve.id}/fiche",
            "/eleves?ajax=1",
            f"/api/eleves/classe/{self.classe.id}",
            f"/api/qr/info/{eleve.id}",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIsNotNone(response.get_json(), path)
            serialized = response.get_data(as_text=True)
            self.assertIn(eleve.matricule, serialized, path)
            self.assertNotIn('"code_parent"', serialized, path)
            self.assertNotIn(eleve.code_parent, serialized, path)
            self.assertNotIn(self.parent.mot_de_passe, serialized, path)
            self.assertNotIn(self.admin.mot_de_passe, serialized, path)
        self.assertEqual(eleve.to_dict()["matricule"], eleve.matricule)
        self.assertNotIn("code_parent", eleve.to_dict())

    def test_badge_public_affiche_matricule_sans_contact_pin_ni_hash(self):
        eleve = self._eleve(code_parent="58310427", parent_id=self.parent.id)
        inscription = self._inscrire(eleve)
        token = generer_token_eleve(self.ecole.id, inscription.id)
        response = self.app.test_client().get(f"/verifier/eleve/{token}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(eleve.matricule, html)
        self.assertNotIn(eleve.code_parent, html)
        self.assertNotIn(self.parent.telephone, html)
        self.assertNotIn(self.parent.mot_de_passe, html)
        self.assertNotIn(self.admin.mot_de_passe, html)


if __name__ == "__main__":
    unittest.main()
