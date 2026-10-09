"""Régressions du lot B sur les vraies routes RH, en base SQLite isolée."""

import json
import unittest
from datetime import date

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Ecole, FichePaiePersonnel, Professeur, Utilisateur


class PaieRHTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-paie-rh-avancee"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestPaieRHSecuriteAvancee(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PaieRHTestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.ecole = Ecole(nom="Ecole RH A", onboarding_complete=True)
        self.autre_ecole = Ecole(nom="Ecole RH B", onboarding_complete=True)
        db.session.add_all([self.ecole, self.autre_ecole])
        db.session.flush()

        self.annee_active = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="active", ecole_id=self.ecole.id,
        )
        self.annee_archivee = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30), statut="archivee", ecole_id=self.ecole.id,
        )
        self.annee_autre_ecole = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30), statut="active", ecole_id=self.autre_ecole.id,
        )
        db.session.add_all([self.annee_active, self.annee_archivee, self.annee_autre_ecole])
        db.session.flush()

        self.admin = Utilisateur(
            nom="Admin", prenom="RH", email="rh-admin-a@example.test",
            role="admin", statut="actif", ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("Test1234!"),
        )
        self.prof_user = Utilisateur(
            nom="Prof", prenom="A", email="rh-prof-a@example.test",
            role="professeur", statut="actif", ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("Test1234!"),
        )
        self.autre_prof_user = Utilisateur(
            nom="Prof", prenom="B", email="rh-prof-b@example.test",
            role="professeur", statut="actif", ecole_id=self.autre_ecole.id,
            mot_de_passe=generate_password_hash("Test1234!"),
        )
        db.session.add_all([self.admin, self.prof_user, self.autre_prof_user])
        db.session.flush()

        self.prof = Professeur(
            nom="Prof", prenom="A", email="rh-prof-a@example.test",
            utilisateur_id=self.prof_user.id, ecole_id=self.ecole.id,
            type_remuneration="fixe", salaire_base=100000.0,
        )
        self.autre_prof = Professeur(
            nom="Prof", prenom="B", email="rh-prof-b@example.test",
            utilisateur_id=self.autre_prof_user.id, ecole_id=self.autre_ecole.id,
            type_remuneration="fixe", salaire_base=90000.0,
        )
        db.session.add_all([self.prof, self.autre_prof])
        db.session.commit()
        self._selectionner_annee(self.annee_active.id)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _selectionner_annee(self, annee_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
            session["ecole_id"] = self.ecole.id
            session["role"] = "admin"
            session["onboarding_complete"] = True
            session[f"onboarding_complete_{self.ecole.id}"] = True
            session["annee_consultee"] = {str(self.ecole.id): annee_id}

    def _post(self, route, **payload):
        return self.client.post(f"/paie-personnel/{route}", json=payload)

    def _evenements(self, fiche):
        historique = (fiche.note or "").split(
            "--- HISTORIQUE DES RÈGLEMENTS (LECTURE SEULE) ---", 1,
        )
        self.assertEqual(len(historique), 2, "La trace des versements doit être conservée")
        return [json.loads(ligne) for ligne in historique[1].splitlines() if ligne.strip()]

    def _creer_fiche(self, *, annee, mois, annee_civile, net, paye=0.0, note=None):
        fiche = FichePaiePersonnel(
            professeur_id=self.prof.id, ecole_id=self.ecole.id,
            annee_scolaire_id=annee.id, mois=mois, annee=annee_civile,
            periode_nom=f"{mois:02d}/{annee_civile}", type_remuneration="fixe",
            salaire_base=net, salaire_brut=net, salaire_net=net,
            montant_paye=paye,
            statut_paiement="partiel" if paye else "en_attente", note=note,
        )
        db.session.add(fiche)
        db.session.commit()
        return fiche

    def test_versements_cumulatifs_solde_statuts_historique_et_trop_percu(self):
        fiche = self._creer_fiche(
            annee=self.annee_active, mois=10, annee_civile=2026, net=100000.0,
            note="Commentaire de paie initial.",
        )

        premier = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=40000,
            mode_reglement="especes", reference_recu="ACOMPTE-40",
        )
        self.assertEqual(premier.status_code, 200, premier.get_data(as_text=True))
        self.assertEqual(premier.json["fiche"]["montant_paye"], 40000.0)
        self.assertEqual(premier.json["fiche"]["reste_a_payer"], 60000.0)
        self.assertEqual(premier.json["fiche"]["statut_paiement"], "partiel")

        second = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=60000,
            mode_reglement="virement", reference_recu="SOLDE-60",
        )
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        self.assertEqual(second.json["fiche"]["montant_paye"], 100000.0)
        self.assertEqual(second.json["fiche"]["reste_a_payer"], 0.0)
        self.assertEqual(second.json["fiche"]["statut_paiement"], "paye")

        db.session.refresh(fiche)
        historique = fiche.note or ""
        self.assertIn("Commentaire de paie initial.", historique)
        self.assertIn("ACOMPTE-40", historique)
        self.assertIn("SOLDE-60", historique)
        self.assertLess(historique.index("ACOMPTE-40"), historique.index("SOLDE-60"))
        evenements = self._evenements(fiche)
        self.assertEqual([(e["type"], e["montant"], e["reference"])
                          for e in evenements], [
            ("versement", "40000.00", "ACOMPTE-40"),
            ("versement", "60000.00", "SOLDE-60"),
        ])
        self.assertEqual(sum(float(e["montant"]) for e in evenements), fiche.montant_paye)
        for evenement in evenements:
            self.assertRegex(evenement["date"], r"^\d{4}-\d{2}-\d{2}")
            self.assertEqual(evenement["utilisateur_id"], self.admin.id)

        excedent = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=1,
            mode_reglement="especes", reference_recu="TROP-PERCU",
        )
        self.assertEqual(excedent.status_code, 400)
        self.assertFalse(excedent.json["success"])
        db.session.refresh(fiche)
        self.assertEqual((fiche.montant_paye, fiche.statut_paiement, fiche.note),
                         (100000.0, "paye", historique))

        ajustement = self._post(
            "ajuster-ligne", fiche_id=fiche.id, primes=0, retenues=0,
            commentaire="Commentaire modifié sans effacer les versements.",
        )
        self.assertEqual(ajustement.status_code, 200)
        db.session.refresh(fiche)
        self.assertIn("ACOMPTE-40", fiche.note)
        self.assertIn("SOLDE-60", fiche.note)

    def test_correction_du_total_est_explicite_et_tracee(self):
        fiche = self._creer_fiche(
            annee=self.annee_active, mois=11, annee_civile=2026, net=100000.0,
        )
        versement = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=40000,
            reference_recu="ERREUR-SAISIE", mode_reglement="especes",
        )
        self.assertEqual(versement.status_code, 200)
        complement = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=10000,
            reference_recu="COMPLEMENT-10", mode_reglement="virement",
        )
        self.assertEqual(complement.status_code, 200)

        sans_motif = self._post(
            "corriger-total-reglement", fiche_id=fiche.id, montant_total=35000,
        )
        self.assertEqual(sans_motif.status_code, 400)
        db.session.refresh(fiche)
        self.assertEqual(fiche.montant_paye, 50000.0)

        correction = self._post(
            "corriger-total-reglement", fiche_id=fiche.id, montant_total=35000,
            motif="Erreur de saisie de 5 000 FCFA", reference_recu="RECTIF-35",
        )
        self.assertEqual(correction.status_code, 200, correction.get_data(as_text=True))
        self.assertEqual(correction.json["fiche"]["montant_paye"], 35000.0)
        self.assertEqual(correction.json["fiche"]["reste_a_payer"], 65000.0)
        self.assertEqual(correction.json["fiche"]["statut_paiement"], "partiel")
        db.session.refresh(fiche)
        self.assertIn("ERREUR-SAISIE", fiche.note)
        self.assertIn("COMPLEMENT-10", fiche.note)
        self.assertIn("RECTIF-35", fiche.note)
        self.assertIn("Erreur de saisie", fiche.note)
        evenements = self._evenements(fiche)
        self.assertEqual([e["type"] for e in evenements],
                         ["versement", "versement", "correction_total"])
        self.assertEqual((evenements[-1]["ancien_total"], evenements[-1]["nouveau_total"]),
                         ("50000.00", "35000.00"))

    def test_versement_sur_solde_legacy_garde_une_trace_honnete(self):
        fiche = self._creer_fiche(
            annee=self.annee_active, mois=10, annee_civile=2026,
            net=100000.0, paye=40000.0, note="Note ancienne.",
        )
        response = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=60000,
            mode_reglement="virement", reference_recu="SOLDE-LEGACY",
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        db.session.refresh(fiche)
        self.assertEqual((fiche.montant_paye, fiche.reste_a_payer, fiche.statut_paiement),
                         (100000.0, 0.0, "paye"))
        self.assertIn("Note ancienne.", fiche.note)
        evenements = self._evenements(fiche)
        self.assertEqual([e["type"] for e in evenements],
                         ["solde_initial_legacy", "versement"])
        self.assertEqual(evenements[0]["montant"], "40000.00")
        self.assertNotIn("date", evenements[0])
        self.assertNotIn("reference", evenements[0])
        self.assertEqual((evenements[1]["montant"], evenements[1]["reference"]),
                         ("60000.00", "SOLDE-LEGACY"))

    def test_archive_consultee_refuse_recalcul_et_archive_ciblee_refuse_mutations(self):
        fiche = self._creer_fiche(
            annee=self.annee_archivee, mois=10, annee_civile=2025,
            net=80000.0, paye=25000.0, note="Bulletin historique.",
        )
        etat = (
            fiche.annee_scolaire_id, fiche.salaire_base, fiche.salaire_net,
            fiche.primes, fiche.deductions, fiche.montant_paye,
            fiche.statut_paiement, fiche.note,
        )

        # Une année active reste consultée : c'est l'année de la fiche qui décide.
        for route, payload in (
            ("ajuster-ligne", {"fiche_id": fiche.id, "primes": 30000,
                               "retenues": 0, "commentaire": "Mutation interdite"}),
            ("enregistrer-reglement", {"fiche_id": fiche.id, "montant_verse": 5000,
                                       "reference_recu": "ARCHIVE-INTERDIT"}),
            ("corriger-total-reglement", {"fiche_id": fiche.id, "montant_total": 30000,
                                           "motif": "Correction interdite"}),
        ):
            with self.subTest(route=route):
                response = self._post(route, **payload)
                self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
                db.session.refresh(fiche)
                self.assertEqual((
                    fiche.annee_scolaire_id, fiche.salaire_base, fiche.salaire_net,
                    fiche.primes, fiche.deductions, fiche.montant_paye,
                    fiche.statut_paiement, fiche.note,
                ), etat)

        # Recalcul global avec l'année archivèe sélectionnée : aucun salaire historique recalculé.
        self._selectionner_annee(self.annee_archivee.id)
        calcul = self._post("calculer-mois", mois=10, annee=2025)
        self.assertEqual(calcul.status_code, 403, calcul.get_data(as_text=True))
        db.session.refresh(fiche)
        self.assertEqual((fiche.salaire_base, fiche.salaire_net, fiche.montant_paye, fiche.note),
                         (80000.0, 80000.0, 25000.0, "Bulletin historique."))
        self.assertEqual(FichePaiePersonnel.query.filter_by(
            ecole_id=self.ecole.id, annee_scolaire_id=self.annee_archivee.id,
        ).count(), 1)

        contrat_initial = (
            self.prof.type_remuneration, self.prof.salaire_base, self.prof.taux_horaire,
        )
        contrat = self._post(
            "configurer-contrat", professeur_id=self.prof.id,
            type_remuneration="fixe", salaire_base=120000,
            taux_horaire=0,
        )
        self.assertEqual(contrat.status_code, 403, contrat.get_data(as_text=True))
        db.session.refresh(self.prof)
        self.assertEqual((
            self.prof.type_remuneration, self.prof.salaire_base, self.prof.taux_horaire,
        ), contrat_initial)

    def test_periode_civile_hors_annee_refusee_mois_limites_acceptes(self):
        invalides = [(1, 1900), (1, 2035), (8, 2026), (7, 2027)]
        for mois, annee in invalides:
            with self.subTest(mois=mois, annee=annee):
                response = self._post("calculer-mois", mois=mois, annee=annee)
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertFalse(response.json["success"])
                self.assertEqual(FichePaiePersonnel.query.filter_by(
                    ecole_id=self.ecole.id, mois=mois, annee=annee,
                ).count(), 0)

        for mois, annee in [(9, 2026), (6, 2027)]:
            with self.subTest(mois=mois, annee=annee):
                response = self._post("calculer-mois", mois=mois, annee=annee)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                fiche = FichePaiePersonnel.query.filter_by(
                    ecole_id=self.ecole.id, annee_scolaire_id=self.annee_active.id,
                    professeur_id=self.prof.id, mois=mois, annee=annee,
                ).one()
                self.assertEqual(fiche.salaire_net, 100000.0)

        page_active = self.client.get("/paie-personnel/?periode=2026-09")
        self.assertEqual(page_active.status_code, 200)
        self.assertIn('id="btnCalculerMois"', page_active.get_data(as_text=True))

    def test_consulter_archive_ne_rattache_pas_une_fiche_ancienne_sans_annee(self):
        fiche = FichePaiePersonnel(
            professeur_id=self.prof.id, ecole_id=self.ecole.id,
            annee_scolaire_id=None, mois=11, annee=2025,
            periode_nom="Novembre 2025", type_remuneration="fixe",
            salaire_base=80000.0, salaire_brut=80000.0, salaire_net=80000.0,
            montant_paye=10000.0, statut_paiement="partiel",
            note="Document historique sans rattachement.",
        )
        db.session.add(fiche)
        db.session.commit()
        self._selectionner_annee(self.annee_archivee.id)

        response = self.client.get("/paie-personnel/?periode=2025-11")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:1000])
        html = response.get_data(as_text=True)
        self.assertIn("Année scolaire archivée", html)
        for action in (
            'id="btnCalculerMois"', "ouvrirModalAjustement",
            "ouvrirModalReglement", "ouvrirModalCorrectionTotal",
        ):
            self.assertNotIn(action, html)
        db.session.refresh(fiche)
        self.assertIsNone(fiche.annee_scolaire_id)
        self.assertEqual((fiche.salaire_net, fiche.montant_paye, fiche.note),
                         (80000.0, 10000.0, "Document historique sans rattachement."))

        recalcul = self._post("calculer-mois", mois=11, annee=2025)
        self.assertEqual(recalcul.status_code, 403)
        reglement = self._post(
            "enregistrer-reglement", fiche_id=fiche.id, montant_verse=5000,
        )
        self.assertEqual(reglement.status_code, 409)
        db.session.refresh(fiche)
        self.assertIsNone(fiche.annee_scolaire_id)
        self.assertEqual(fiche.montant_paye, 10000.0)

    def test_recalcul_rattache_legacy_seulement_si_annee_unique_active(self):
        fiche = FichePaiePersonnel(
            professeur_id=self.prof.id, ecole_id=self.ecole.id,
            annee_scolaire_id=None, mois=10, annee=2026,
            periode_nom="Octobre 2026", type_remuneration="fixe",
            salaire_base=80000.0, salaire_brut=80000.0, salaire_net=80000.0,
            montant_paye=0.0, statut_paiement="en_attente",
        )
        db.session.add(fiche)
        db.session.commit()

        lecture = self.client.get("/paie-personnel/?periode=2026-10")
        self.assertEqual(lecture.status_code, 200)
        db.session.refresh(fiche)
        self.assertIsNone(fiche.annee_scolaire_id)

        recalcul = self._post("calculer-mois", mois=10, annee=2026)
        self.assertEqual(recalcul.status_code, 200, recalcul.get_data(as_text=True))
        db.session.refresh(fiche)
        self.assertEqual(fiche.annee_scolaire_id, self.annee_active.id)
        self.assertEqual(fiche.salaire_net, 100000.0)
        self.assertEqual(FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof.id, mois=10, annee=2026,
        ).count(), 1)

    def test_recalcul_refuse_legacy_si_deux_annees_chevauchent(self):
        autre_annee = AnneeScolaire(
            nom="2026-2028", date_debut=date(2026, 9, 1),
            date_fin=date(2028, 6, 30), statut="planifiee", ecole_id=self.ecole.id,
        )
        db.session.add(autre_annee)
        fiche = FichePaiePersonnel(
            professeur_id=self.prof.id, ecole_id=self.ecole.id,
            annee_scolaire_id=None, mois=12, annee=2026,
            periode_nom="Décembre 2026", type_remuneration="fixe",
            salaire_base=80000.0, salaire_brut=80000.0, salaire_net=80000.0,
            montant_paye=0.0, statut_paiement="en_attente",
        )
        db.session.add(fiche)
        db.session.commit()

        recalcul = self._post("calculer-mois", mois=12, annee=2026)
        self.assertEqual(recalcul.status_code, 409, recalcul.get_data(as_text=True))
        db.session.refresh(fiche)
        self.assertIsNone(fiche.annee_scolaire_id)
        self.assertEqual(fiche.salaire_net, 80000.0)

    def test_fiche_autre_ecole_inaccessible_aux_mutations(self):
        fiche = FichePaiePersonnel(
            professeur_id=self.autre_prof.id, ecole_id=self.autre_ecole.id,
            annee_scolaire_id=self.annee_autre_ecole.id,
            mois=10, annee=2026, periode_nom="Octobre 2026",
            type_remuneration="fixe", salaire_base=90000.0,
            salaire_brut=90000.0, salaire_net=90000.0,
            montant_paye=10000.0, statut_paiement="partiel",
        )
        db.session.add(fiche)
        db.session.commit()
        for route, payload in (
            ("ajuster-ligne", {"fiche_id": fiche.id, "primes": 5000}),
            ("enregistrer-reglement", {"fiche_id": fiche.id, "montant_verse": 5000}),
            ("corriger-total-reglement", {"fiche_id": fiche.id,
                                           "montant_total": 20000, "motif": "Test"}),
        ):
            with self.subTest(route=route):
                response = self._post(route, **payload)
                self.assertEqual(response.status_code, 404, response.get_data(as_text=True))
                db.session.refresh(fiche)
                self.assertEqual((fiche.salaire_net, fiche.primes, fiche.montant_paye),
                                 (90000.0, 0.0, 10000.0))


if __name__ == "__main__":
    unittest.main()
