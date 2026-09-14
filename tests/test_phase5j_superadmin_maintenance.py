"""
Tests KLASORA — Phase 5J : Super Admin + Maintenance + Backup/Restore.
Exécution de 8 tests ciblés.
"""
from datetime import date
import json
import os
import unittest

from app import create_app, db
from app.admin.scripts import (
    create_school_backup,
    inspect_school_backup,
    restore_school_backup,
)
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    Log,
    NiveauScolaire,
    Note,
    Paiement,
    Utilisateur,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import ensure_standard_niveaux


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5j"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


def _user(email, role, ecole_id, password="test"):
    u = Utilisateur(nom="TestUser", email=email, mot_de_passe=password, role=role, ecole_id=ecole_id)
    db.session.add(u)
    db.session.flush()
    return u


def _annee(ecole_id, statut="active", nom="2024-2025"):
    a = AnneeScolaire(
        nom=nom,
        date_debut=date(2024, 9, 1),
        date_fin=date(2025, 6, 30),
        statut=statut,
        ecole_id=ecole_id,
    )
    db.session.add(a)
    db.session.flush()
    return a


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


def _eleve(ecole_id, nom, prenom, classe_id=None):
    e = Eleve(
        nom=nom,
        prenom=prenom,
        genre="M",
        statut="actif",
        ecole_id=ecole_id,
        classe_id=classe_id,
        date_naissance=date(2012, 1, 1),
    )
    db.session.add(e)
    db.session.flush()
    return e


class Phase5JSuperAdminMaintenanceTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_standard_niveaux(commit=True)

        self.ecole_a = Ecole(nom="Ecole Alpha", statut="active")
        self.ecole_b = Ecole(nom="Ecole Beta", statut="active")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.ecole_a.setup_complete = True
        self.ecole_b.setup_complete = True

        self.superadmin = _user("super@klasora.com", "super_admin", None)
        self.admin_a = _user("admin_a@alpha.com", "admin", self.ecole_a.id)
        self.admin_b = _user("admin_b@beta.com", "admin", self.ecole_b.id)

        self.annee_a = _annee(self.ecole_a.id)
        self.annee_b = _annee(self.ecole_b.id)

        niv = NiveauScolaire.query.first()
        if niv:
            db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id, niveau_id=niv.id, actif=True))
            db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id, niveau_id=niv.id, actif=True))
            db.session.flush()

        self.classe_a = _classe(self.ecole_a.id, self.annee_a.id, "6e A", niv.nom if niv else "6eme")
        self.classe_b = _classe(self.ecole_b.id, self.annee_b.id, "6e B", niv.nom if niv else "6eme")

        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # 1. Admin école interdit d'accéder aux routes Super Admin
    def test_01_admin_ecole_interdit_route_super_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)

        resp = self.client.get("/admin/maintenance")
        self.assertIn(resp.status_code, (403, 302))

        resp_ecoles = self.client.get("/admin/ecoles")
        self.assertIn(resp_ecoles.status_code, (403, 302))

    # 2. École A ne peut pas gérer École B
    def test_02_ecole_a_ne_peut_pas_gerer_ecole_b(self):
        eleve_b = _eleve(self.ecole_b.id, "Diop", "Modou", self.classe_b.id)
        db.session.commit()

        from app.authorization import can_access_eleve
        with self.app.test_request_context():
            with self.client.session_transaction() as sess:
                sess["_user_id"] = str(self.admin_a.id)
            # L'admin A ne peut pas accéder à l'élève de l'école B
            self.assertFalse(can_access_eleve(eleve_b))

    # 3. École suspendue bloque un utilisateur connecté
    def test_03_ecole_suspendue_bloque_utilisateur_connecte(self):
        self.ecole_a.statut = "suspendu"
        self.ecole_a.motif_blocage = "Non paiement des frais"
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)

        resp = self.client.get("/eleves", follow_redirects=True)
        # Utilisateur déconnecté ou redirigé vers login avec message de suspension
        self.assertIn("suspendu", resp.get_data(as_text=True).lower())

    # 4. Maintenance École A n'affecte pas École B et Super Admin reste autorisé
    def test_04_maintenance_ecole_a_n_affecte_pas_ecole_b_et_super_admin_autorise(self):
        self.ecole_a.statut = "maintenance"
        db.session.commit()

        # Admin École A bloqué avec page de maintenance
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_a.id)
        resp_a = self.client.get("/eleves")
        self.assertEqual(resp_a.status_code, 503)

        # Admin École B accède normalement à son école
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_b.id)
        resp_b = self.client.get("/eleves")
        self.assertEqual(resp_b.status_code, 200)

        # Super Admin accède à la plateforme sans restriction
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.superadmin.id)
        resp_super = self.client.get("/admin/ecoles")
        self.assertEqual(resp_super.status_code, 200)

    # 5. Backup réel créé avec données
    def test_05_backup_reel_cree_avec_donnees(self):
        eleve = _eleve(self.ecole_a.id, "Ndiaye", "Awa", self.classe_a.id)
        creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_a.id, self.classe_a.id)
        db.session.commit()

        backup_file = create_school_backup(self.ecole_a.id)
        self.assertTrue(os.path.exists(backup_file))

        metadata, data = inspect_school_backup(os.path.basename(backup_file))
        self.assertEqual(metadata["ecole_id"], self.ecole_a.id)
        self.assertEqual(metadata["counts"]["eleves"], 1)

    # 6. Suppression DEV + Restore -> Données principales restaurées (TEST PRIORITAIRE)
    def test_06_suppression_dev_plus_restore_donnees_restaurees(self):
        from datetime import time
        from app.models import (
            Professeur, Cours, EmploiTemps, PeriodeBulletin, professeur_classes
        )

        # A. Créer des données complètes incluant EmploiTemps, PeriodeBulletin, Professeur + affectation
        prof = Professeur(
            nom="Koné", prenom="Bakary", email="kone@alpha.com",
            utilisateur_id=self.admin_a.id, ecole_id=self.ecole_a.id
        )
        db.session.add(prof)
        db.session.flush()

        # Affectation professeur <-> classe via table d'association professeur_classes
        db.session.execute(professeur_classes.insert().values(
            professeur_id=prof.id, classe_id=self.classe_a.id, ecole_id=self.ecole_a.id
        ))

        cours = Cours(nom="Mathématiques", ecole_id=self.ecole_a.id, classe_id=self.classe_a.id, professeur_id=prof.id)
        db.session.add(cours)
        db.session.flush()

        emploi = EmploiTemps(
            professeur_id=prof.id, jour="Lundi", heure_debut=time(8, 0), heure_fin=time(10, 0),
            cours_id=cours.id, classe_id=self.classe_a.id, ecole_id=self.ecole_a.id
        )
        periode = PeriodeBulletin(nom="Semestre 1", annee_id=self.annee_a.id, ecole_id=self.ecole_a.id, publie=True)
        db.session.add_all([emploi, periode])

        eleve = _eleve(self.ecole_a.id, "Diallo", "Moussa", self.classe_a.id)
        insc, _ = creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_a.id, self.classe_a.id)
        p = Paiement(montant=50000.0, date_paiement=date(2024, 10, 1), mois="Octobre", annee=2024, eleve_id=eleve.id, ecole_id=self.ecole_a.id)
        db.session.add(p)
        db.session.commit()

        # Counts avant backup
        count_eleves_before = Eleve.query.filter_by(ecole_id=self.ecole_a.id).count()
        count_emplois_before = EmploiTemps.query.filter_by(ecole_id=self.ecole_a.id).count()
        count_periodes_before = PeriodeBulletin.query.filter_by(ecole_id=self.ecole_a.id).count()
        count_prof_classes_before = len(db.session.execute(
            professeur_classes.select().where(professeur_classes.c.ecole_id == self.ecole_a.id)
        ).all())

        # B. Sauvegarder
        backup_file = create_school_backup(self.ecole_a.id)
        filename = os.path.basename(backup_file)

        # C. Supprimer volontairement les données DEV de l'école A
        db.session.execute(professeur_classes.delete().where(professeur_classes.c.ecole_id == self.ecole_a.id))
        EmploiTemps.query.filter_by(ecole_id=self.ecole_a.id).delete()
        Cours.query.filter_by(ecole_id=self.ecole_a.id).delete()
        PeriodeBulletin.query.filter_by(ecole_id=self.ecole_a.id).delete()
        Paiement.query.filter_by(ecole_id=self.ecole_a.id).delete()
        Inscription.query.filter_by(ecole_id=self.ecole_a.id).delete()
        Eleve.query.filter_by(ecole_id=self.ecole_a.id).delete()
        Professeur.query.filter_by(ecole_id=self.ecole_a.id).delete()
        db.session.commit()

        self.assertEqual(Eleve.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)

        # D. Restaurer le backup
        res = restore_school_backup(filename, target_ecole_id=self.ecole_a.id, confirmation_code="RESTAURER")
        self.assertTrue(res)

        # E. Vérifier la présence des données restaurées et la parité des counts
        self.assertEqual(Eleve.query.filter_by(ecole_id=self.ecole_a.id).count(), count_eleves_before)
        eleve_restaure = Eleve.query.filter_by(ecole_id=self.ecole_a.id, nom="Diallo").first()
        self.assertIsNotNone(eleve_restaure)
        self.assertEqual(Paiement.query.filter_by(ecole_id=self.ecole_a.id).count(), 1)
        self.assertEqual(EmploiTemps.query.filter_by(ecole_id=self.ecole_a.id).count(), count_emplois_before)
        self.assertEqual(PeriodeBulletin.query.filter_by(ecole_id=self.ecole_a.id).count(), count_periodes_before)

        prof_classes_after = len(db.session.execute(
            professeur_classes.select().where(professeur_classes.c.ecole_id == self.ecole_a.id)
        ).all())
        self.assertEqual(prof_classes_after, count_prof_classes_before)

    # 7. Backup corrompu -> Restore refusé
    def test_07_backup_corrompu_restore_refuse(self):
        backup_file = create_school_backup(self.ecole_a.id)
        filename = os.path.basename(backup_file)

        # Altérer le contenu du fichier de sauvegarde
        with open(backup_file, "r", encoding="utf-8") as f:
            content = json.load(f)

        content["data"]["eleves"] = [{"id": 999, "nom": "HACKED"}]
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(content, f)

        # La tentative de restauration doit être refusée pour checksum invalide
        with self.assertRaises(ValueError) as cm:
            restore_school_backup(filename, target_ecole_id=self.ecole_a.id, confirmation_code="RESTAURER")
        self.assertIn("corrompu", str(cm.exception).lower())

    # 8. Action sensible journalisée sans secret
    def test_08_action_sensible_journalisee_sans_secret(self):
        from app.middleware import log_action
        log_action("SUSPENSION", f"Ecole ID={self.ecole_a.id} suspendue par admin", user_id=self.superadmin.id)

        entry = Log.query.filter_by(action="Ecole ID=1 suspendue par admin").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.module, "SUSPENSION")
        # Vérifier qu'aucun mot de passe ou secret n'est présent dans les logs
        self.assertNotIn("mot_de_passe", entry.action.lower())

    # 9. Rétention : 4 backups automatiques à dates différentes -> seuls les 3 plus récents restent
    def test_09_retention_trois_backups_automatiques_max(self):
        from app.admin.scripts import BACKUP_DIR, cleanup_old_automatic_backups
        from datetime import datetime, timedelta

        # Purger les éventuels backups résiduels des tests précédents pour l'école A
        for file in os.listdir(BACKUP_DIR):
            if file.startswith(f"school_{self.ecole_a.id}_"):
                try:
                    os.remove(os.path.join(BACKUP_DIR, file))
                except OSError:
                    pass

        files_created = []
        for i in range(4):
            dt = datetime.now() - timedelta(days=4 - i)
            ts = dt.strftime("%Y%m%d_%H%M%S")
            fname = f"school_{self.ecole_a.id}_{ts}_automatic.json"
            fpath = os.path.join(BACKUP_DIR, fname)
            data = {
                "metadata": {
                    "type": "school",
                    "backup_type": "automatic",
                    "ecole_id": self.ecole_a.id,
                    "created_at": dt.isoformat(),
                    "timestamp": ts,
                    "checksum": "dummy"
                },
                "data": {"ecole": {"id": self.ecole_a.id}}
            }
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f)
            files_created.append(fpath)

        deleted = cleanup_old_automatic_backups(self.ecole_a.id, keep=3)
        self.assertEqual(deleted, 1)

        self.assertFalse(os.path.exists(files_created[0]))
        self.assertTrue(os.path.exists(files_created[1]))
        self.assertTrue(os.path.exists(files_created[2]))
        self.assertTrue(os.path.exists(files_created[3]))

    # 10. Isolation des sauvegardes entre écoles : nettoyage A ne touche pas B
    def test_10_nettoyage_ecole_a_n_affecte_pas_ecole_b(self):
        from app.admin.scripts import BACKUP_DIR, cleanup_old_automatic_backups
        from datetime import datetime, timedelta

        for i in range(4):
            dt = datetime.now() - timedelta(days=4 - i)
            ts = dt.strftime("%Y%m%d_%H%M%S")
            fpath = os.path.join(BACKUP_DIR, f"school_{self.ecole_a.id}_{ts}_automatic.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"type": "school", "backup_type": "automatic", "ecole_id": self.ecole_a.id, "created_at": dt.isoformat()}}, f)

        b_files = []
        for i in range(3):
            dt = datetime.now() - timedelta(days=3 - i)
            ts = dt.strftime("%Y%m%d_%H%M%S")
            fpath = os.path.join(BACKUP_DIR, f"school_{self.ecole_b.id}_{ts}_automatic.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"type": "school", "backup_type": "automatic", "ecole_id": self.ecole_b.id, "created_at": dt.isoformat()}}, f)
            b_files.append(fpath)

        cleanup_old_automatic_backups(self.ecole_a.id, keep=3)

        for bf in b_files:
            self.assertTrue(os.path.exists(bf))

    # 11. Idempotence : deux exécutions le même jour -> un seul backup automatique
    def test_11_idempotence_sauvegarde_automatique_par_jour(self):
        f1 = create_school_backup(self.ecole_a.id, backup_type="automatic")
        f2 = create_school_backup(self.ecole_a.id, backup_type="automatic")

        self.assertEqual(f1, f2)

    # 12. Backup manual / safety_restore hors rotation automatic
    def test_12_backup_manuel_et_safety_restore_hors_rotation(self):
        from app.admin.scripts import BACKUP_DIR, cleanup_old_automatic_backups

        for i in range(3):
            fpath = os.path.join(BACKUP_DIR, f"school_{self.ecole_a.id}_auto_{i}.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"type": "school", "backup_type": "automatic", "ecole_id": self.ecole_a.id, "created_at": f"2026-09-0{i+1}T10:00:00"}}, f)

        manual_path = create_school_backup(self.ecole_a.id, backup_type="manual")
        safety_path = create_school_backup(self.ecole_a.id, backup_type="safety_restore")

        cleanup_old_automatic_backups(self.ecole_a.id, keep=3)

        self.assertTrue(os.path.exists(manual_path))
        self.assertTrue(os.path.exists(safety_path))


if __name__ == "__main__":
    unittest.main()
