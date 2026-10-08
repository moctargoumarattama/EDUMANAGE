"""Sauvegardes transactionnelles et liens permanents des certificats.

Toutes les bases, copies et sauvegardes restent dans des répertoires temporaires.
Les appels PostgreSQL sont simulés et ne contactent aucun serveur.
"""
import json
import logging
import shutil
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import create_app, db
from app.admin import scripts
from app.config import Config
from app.models import (
    AnneeScolaire, CertificatAdministratif, Classe, Cours, Ecole, Eleve,
    FichePaiePersonnel, Inscription, Log, PointagePersonnel, Professeur, Utilisateur,
    gestion_ecole, professeur_classes,
)


@pytest.fixture
def backup_app(tmp_path, monkeypatch):
    class TemporaryBackupConfig(Config):
        TESTING = True
        APP_ENV = "testing"
        SECRET_KEY = "test-sauvegardes-robustesse"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "source.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": NullPool}

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(scripts, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(scripts, "DB_PATH", str(tmp_path / "source.db"))
    monkeypatch.setenv("BACKUP_MIN_FREE_MB", "0")
    app = create_app(TemporaryBackupConfig)
    with app.app_context():
        # Ne pas dépendre du réglage SQLite qui désactive généralement les FK.
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        event.listen(db.engine, "connect", enable_foreign_keys)
        db.create_all()
        try:
            yield app
        finally:
            db.session.remove()
            db.engine.dispose()


@pytest.fixture
def certificats_ecole(backup_app):
    ecole = Ecole(nom="Ecole restauration", onboarding_complete=True, ville="Niamey")
    autre_ecole = Ecole(nom="Autre ecole", onboarding_complete=True)
    db.session.add_all([ecole, autre_ecole])
    db.session.flush()
    eleves = [
        Eleve(
            nom="SOW", prenom=prenom, date_naissance=date(2013, 1, jour),
            date_inscription=datetime(2026, 9, 1), annee_premiere_ecole=2026,
            ecole_id=ecole.id,
        )
        for prenom, jour in (("Awa", 1), ("Moussa", 2))
    ]
    eleve_externe = Eleve(
        nom="DIALLO", prenom="Autre", date_naissance=date(2012, 4, 3),
        annee_premiere_ecole=2026, ecole_id=autre_ecole.id,
    )
    db.session.add_all([*eleves, eleve_externe])
    db.session.flush()
    assert [eleve.matricule for eleve in eleves] == ["26-0001", "26-0002"]
    assert eleve_externe.matricule == "26-0001"

    certificats = []
    for index, (eleve, type_certificat) in enumerate(zip(eleves, ("scolarite", "transfert")), 1):
        certificat = CertificatAdministratif(
            ecole_id=ecole.id, eleve_id=eleve.id, type_certificat=type_certificat,
            reference=f"{'CS' if index == 1 else 'CR'}-2026-{index:04d}",
            annee_scolaire="2026-2027", classe_nom="6e A", niveau="College",
            type_admission="Reinscription", date_depart=date(2026, 10, 8) if index == 2 else None,
            etablissement_destination="College destination" if index == 2 else None,
            ville_emission="Niamey", date_emission=date(2026, 10, 8),
            signataire_nom="Mme Direction", signataire_titre="La Directrice",
            code_verification=f"qr-original-{index}", created_at=datetime(2026, 10, 8, 9, index),
        )
        db.session.add(certificat)
        certificats.append(certificat)
    externe = CertificatAdministratif(
        ecole_id=autre_ecole.id, eleve_id=eleve_externe.id, type_certificat="scolarite",
        reference="CS-2026-EXTERNE", annee_scolaire="2026-2027", classe_nom="5e",
        signataire_nom="Autre Direction", code_verification="qr-autre-ecole",
    )
    db.session.add(externe)
    db.session.commit()
    resultat = {
        "ecole_id": ecole.id,
        "eleve_ids": [eleve.id for eleve in eleves],
        "certificats": [scripts._serialize_instance(certificat) for certificat in certificats],
        "externe": scripts._serialize_instance(externe),
    }
    db.session.expunge_all()
    return resultat


def _reecrire_snapshot(path, modifier):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    modifier(snapshot["data"])
    snapshot["metadata"]["checksum"] = scripts._compute_backup_checksum(snapshot["data"])
    Path(path).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return snapshot


def _verifier_certificats_restaures(app, donnees, nouveaux_ids=None):
    nouveaux_ids = nouveaux_ids or donnees["eleve_ids"]
    certificats = CertificatAdministratif.query.filter_by(ecole_id=donnees["ecole_id"]).all()
    assert len(certificats) == 2
    for index, original in enumerate(donnees["certificats"]):
        certificat = next(c for c in certificats if c.reference == original["reference"])
        assert certificat.eleve_id == nouveaux_ids[index]
        assert certificat.eleve.matricule == f"26-{index + 1:04d}"
        assert certificat.eleve.prenom == ("Awa", "Moussa")[index]
        actuel = scripts._serialize_instance(certificat)
        for champ, valeur in original.items():
            if champ not in {"id", "eleve_id"}:
                assert actuel[champ] == valeur, champ
        reponse = app.test_client().get(f"/certificats/verifier/{original['code_verification']}")
        assert reponse.status_code == 200
        assert original["reference"] in reponse.get_data(as_text=True)
    externe = CertificatAdministratif.query.filter_by(reference=donnees["externe"]["reference"]).one()
    assert scripts._serialize_instance(externe) == donnees["externe"]
    with db.engine.connect() as connexion:
        assert connexion.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_backup_sqlite_checkpoint_wal_et_zero_perte(backup_app, tmp_path, monkeypatch):
    source_path = Path(db.engine.url.database)
    connexion = sqlite3.connect(source_path)
    try:
        assert connexion.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connexion.execute("PRAGMA wal_autocheckpoint=0")
        connexion.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connexion.execute("INSERT INTO ecole (nom) VALUES (?)", ("Derniere ligne WAL",))
        connexion.commit()
        assert source_path.with_name(source_path.name + "-wal").stat().st_size > 0

        # Confirme que ce cas reproduit réellement la perte avec l'ancienne copie.
        copie_incomplete = tmp_path / "copie_fichier_principal.db"
        shutil.copyfile(source_path, copie_incomplete)
        with closing(sqlite3.connect(copie_incomplete)) as copie:
            assert copie.execute("SELECT count(*) FROM ecole").fetchone()[0] == 0

        commandes = []
        connecter = sqlite3.connect

        def connecter_avec_trace(*args, **kwargs):
            connection = connecter(*args, **kwargs)
            connection.set_trace_callback(commandes.append)
            return connection

        monkeypatch.setattr(scripts.sqlite3, "connect", connecter_avec_trace)
        sauvegarde = scripts.create_backup()
        assert any("WAL_CHECKPOINT(TRUNCATE)" in commande.upper() for commande in commandes)
        with closing(connecter(sauvegarde)) as copie:
            assert copie.execute("SELECT nom FROM ecole").fetchall() == [("Derniere ligne WAL",)]
            assert copie.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connexion.close()


def test_backup_sqlite_inclut_ecriture_apres_checkpoint(backup_app, monkeypatch):
    """Une écriture entre checkpoint et copie est incluse grâce à Connection.backup."""
    writer = sqlite3.connect(db.engine.url.database)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO ecole (nom) VALUES ('Avant checkpoint')")
    writer.commit()
    connecter = sqlite3.connect
    appels_backup = []

    class ConcurrentBackupConnection(sqlite3.Connection):
        def backup(self, target, *args, **kwargs):
            appels_backup.append(True)
            writer.execute("INSERT INTO ecole (nom) VALUES ('Apres checkpoint')")
            writer.commit()
            return super().backup(target, *args, **kwargs)

    def connecter_avec_ecriture_concurrente(*args, **kwargs):
        kwargs["factory"] = ConcurrentBackupConnection
        return connecter(*args, **kwargs)

    try:
        monkeypatch.setattr(scripts.sqlite3, "connect", connecter_avec_ecriture_concurrente)
        sauvegarde = scripts.create_backup()
        assert appels_backup, "La sauvegarde doit utiliser l'API native SQLite"
        with closing(connecter(sauvegarde)) as copie:
            assert copie.execute("SELECT nom FROM ecole ORDER BY id").fetchall() == [
                ("Avant checkpoint",), ("Apres checkpoint",),
            ]
    finally:
        writer.close()


def test_backup_ne_committe_pas_modifications_metier_en_attente(backup_app):
    ecole = Ecole(nom="Modification non validee")
    db.session.add(ecole)
    sauvegarde = scripts.create_backup()
    assert ecole in db.session.new
    assert ecole.id is None
    with closing(sqlite3.connect(sauvegarde)) as copie:
        assert copie.execute("SELECT count(*) FROM ecole").fetchone()[0] == 0
    db.session.rollback()


def test_sauvegarde_ecole_ne_committe_pas_nouvelle_ecole_en_attente(backup_app, certificats_ecole):
    """La vraie journalisation reste indépendante des modifications métier."""
    pending = Ecole(nom="Nouvelle ecole non validee")
    db.session.add(pending)
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])
    assert pending in db.session.new
    assert pending.id is None
    snapshot = json.loads(Path(chemin).read_text(encoding="utf-8"))
    assert snapshot["data"]["ecole"]["id"] == certificats_ecole["ecole_id"]
    assert len(snapshot["data"]["eleves"]) == 2
    assert len(snapshot["data"]["certificats_administratifs"]) == 2
    with closing(sqlite3.connect(db.engine.url.database)) as connexion:
        assert connexion.execute("SELECT count(*) FROM ecole").fetchone()[0] == 2
        assert connexion.execute("SELECT count(*) FROM log WHERE module='SAUVEGARDE'").fetchone()[0] == 1
    db.session.rollback()


def test_restaurer_ecole_refuse_modifications_en_attente_avant_suppression(backup_app, certificats_ecole):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])
    fichiers_avant = sorted(Path(scripts.BACKUP_DIR).iterdir())
    pending = Ecole(nom="Travail metier en attente")
    db.session.add(pending)
    commandes = []

    def noter_commande(_connection, _cursor, statement, _parameters, _context, _executemany):
        commandes.append(statement)

    event.listen(db.engine, "before_cursor_execute", noter_commande)
    try:
        with pytest.raises(RuntimeError, match="modifications en cours"):
            scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    finally:
        event.remove(db.engine, "before_cursor_execute", noter_commande)
    assert pending in db.session.new
    assert pending.id is None
    assert not any(commande.lstrip().upper().startswith(("DELETE", "UPDATE", "INSERT")) for commande in commandes)
    assert sorted(Path(scripts.BACKUP_DIR).iterdir()) == fichiers_avant
    db.session.rollback()
    assert Ecole.query.count() == 2
    assert Eleve.query.count() == 3
    _verifier_certificats_restaures(backup_app, certificats_ecole)


def test_export_certificats_contient_matricule_et_metadonnees(backup_app, certificats_ecole):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])
    snapshot = json.loads(Path(chemin).read_text(encoding="utf-8"))
    certificats = snapshot["data"]["certificats_administratifs"]
    assert snapshot["metadata"]["counts"]["certificats_administratifs"] == 2
    assert len(certificats) == 2
    for index, original in enumerate(certificats_ecole["certificats"]):
        row = next(c for c in certificats if c["reference"] == original["reference"])
        assert row["matricule_eleve"] == f"26-{index + 1:04d}"
        for champ, valeur in original.items():
            assert row[champ] == valeur
    assert scripts.inspect_school_backup(Path(chemin).name)[0]["checksum"]


def test_restaurer_certificats_par_matricule_ids_eleves_reassignes(backup_app, certificats_ecole):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])

    def changer_ids(data):
        for eleve in data["eleves"]:
            eleve["id"] = 99 + int(eleve["matricule"].split("-")[1])
        for index, certificat in enumerate(data["certificats_administratifs"]):
            certificat["eleve_id"] = certificats_ecole["eleve_ids"][1 - index]
            # Un ancien ID de certificat peut être occupé dans une autre école.
            certificat["id"] = certificats_ecole["externe"]["id"]

    _reecrire_snapshot(chemin, changer_ids)
    assert scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    db.session.expire_all()
    _verifier_certificats_restaures(backup_app, certificats_ecole, nouveaux_ids=[100, 101])


def test_certificat_eleve_introuvable_avertissement_et_ignore(backup_app, certificats_ecole, caplog):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])

    def eleve_absent(data):
        data["certificats_administratifs"][0]["matricule_eleve"] = "26-0999"

    _reecrire_snapshot(chemin, eleve_absent)
    with caplog.at_level(logging.WARNING):
        assert scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    assert "26-0999" in caplog.text
    assert certificats_ecole["certificats"][0]["reference"] in caplog.text
    certificats = CertificatAdministratif.query.filter_by(ecole_id=certificats_ecole["ecole_id"]).all()
    assert len(certificats) == 1
    assert certificats[0].reference == certificats_ecole["certificats"][1]["reference"]
    assert certificats[0].eleve.matricule == "26-0002"
    assert Eleve.query.filter_by(ecole_id=certificats_ecole["ecole_id"]).count() == 2


def test_ancienne_sauvegarde_sans_certificats_preserve_qr_actuels(backup_app, certificats_ecole):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])
    _reecrire_snapshot(chemin, lambda data: data.pop("certificats_administratifs"))
    assert scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    db.session.expire_all()
    _verifier_certificats_restaures(backup_app, certificats_ecole)


def test_sauvegarde_liste_certificats_vide_restaure_etat_vide(backup_app, certificats_ecole):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])
    _reecrire_snapshot(chemin, lambda data: data.update(certificats_administratifs=[]))
    assert scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    assert CertificatAdministratif.query.filter_by(ecole_id=certificats_ecole["ecole_id"]).count() == 0
    assert CertificatAdministratif.query.filter_by(reference=certificats_ecole["externe"]["reference"]).count() == 1


@pytest.mark.parametrize("champ", ["reference", "code_verification"])
def test_certificat_conflit_autre_ecole_refuse_sans_perte(backup_app, certificats_ecole, champ):
    chemin = scripts.create_school_backup(certificats_ecole["ecole_id"])

    def conflit(data):
        data["certificats_administratifs"][0][champ] = certificats_ecole["externe"][champ]

    _reecrire_snapshot(chemin, conflit)
    vivant = db.session.get(Eleve, certificats_ecole["eleve_ids"][0])
    vivant.prenom = "Etat vivant a conserver"
    db.session.commit()
    with pytest.raises(ValueError):
        scripts.restore_school_backup(Path(chemin).name, target_ecole_id=certificats_ecole["ecole_id"])
    assert db.session.get(Eleve, certificats_ecole["eleve_ids"][0]).prenom == "Etat vivant a conserver"
    assert Eleve.query.count() == 3
    assert CertificatAdministratif.query.count() == 3
    for original in [*certificats_ecole["certificats"], certificats_ecole["externe"]]:
        certificat = CertificatAdministratif.query.filter_by(reference=original["reference"]).one()
        assert scripts._serialize_instance(certificat) == original


@pytest.mark.parametrize("ancienne_sauvegarde", [False, True])
def test_restaurer_graph_complet_fk_actives_sans_perte_personnel_et_logs(
    backup_app, certificats_ecole, ancienne_sauvegarde,
):
    ecole_id = certificats_ecole["ecole_id"]
    admin = Utilisateur(
        nom="Direction", prenom="Amina", role="admin", ecole_id=ecole_id,
        email="direction@restauration.test", mot_de_passe="hash-test-direction",
    )
    utilisateur_prof = Utilisateur(
        nom="Professeur", prenom="Ali", role="professeur", ecole_id=ecole_id,
        email="prof@restauration.test", mot_de_passe="hash-test-professeur",
    )
    gestionnaire_global = Utilisateur(
        nom="Gestionnaire global", role="super_admin", ecole_id=None,
        email="global@restauration.test", mot_de_passe="hash-test-global",
    )
    annee = AnneeScolaire(
        nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31),
        statut="active", ecole_id=ecole_id,
    )
    db.session.add_all([admin, utilisateur_prof, gestionnaire_global, annee])
    db.session.flush()
    professeur = Professeur(
        nom="Professeur", prenom="Ali", ecole_id=ecole_id,
        utilisateur_id=utilisateur_prof.id, salaire_base=75000,
    )
    db.session.add(professeur)
    db.session.flush()
    classe = Classe(
        nom="6e A", niveau="College", ecole_id=ecole_id,
        professeur_id=professeur.id, annee_scolaire_id=annee.id,
    )
    db.session.add(classe)
    db.session.flush()
    cours = Cours(
        nom="Mathematiques", ecole_id=ecole_id, classe_id=classe.id,
        professeur_id=professeur.id, coefficient=3,
    )
    db.session.add(cours)
    db.session.flush()
    inscription = Inscription(
        ecole_id=ecole_id, eleve_id=certificats_ecole["eleve_ids"][0],
        classe_id=classe.id, cours_id=cours.id, annee_scolaire_id=annee.id,
        frais_annuels=90000, statut="inscrit",
    )
    pointage = PointagePersonnel(
        professeur_id=professeur.id, ecole_id=ecole_id, annee_scolaire_id=annee.id,
        date_pointage=date(2026, 10, 8), statut="retard", retard_minutes=15,
        heures_prevues=4, heures_effectuees=3.75, motif="Pointage valide",
        pointe_par_id=admin.id, valide=True, valide_par_user_id=admin.id,
        date_validation=datetime(2026, 10, 8, 12),
    )
    fiche = FichePaiePersonnel(
        professeur_id=professeur.id, ecole_id=ecole_id, annee_scolaire_id=annee.id,
        mois=10, annee=2026, periode_nom="Octobre 2026", salaire_base=75000,
        salaire_brut=75000, salaire_net=73000, deductions=2000,
        statut_paiement="paye", montant_paye=73000, cree_par_id=admin.id,
        date_paiement=date(2026, 10, 8), reference_paiement="PAIE-ORIGINALE",
    )
    journal = Log(
        level="INFO", module="POINTAGE", action="Validation historique",
        ecole_id=ecole_id, utilisateur_id=utilisateur_prof.id,
    )
    db.session.add_all([inscription, pointage, fiche, journal])
    affectations = [
        {"utilisateur_id": admin.id, "ecole_id": ecole_id},
        {"utilisateur_id": admin.id, "ecole_id": certificats_ecole["externe"]["ecole_id"]},
        {"utilisateur_id": gestionnaire_global.id, "ecole_id": ecole_id},
    ]
    db.session.execute(gestion_ecole.insert(), affectations)
    db.session.execute(professeur_classes.insert().values(
        professeur_id=professeur.id, classe_id=classe.id, ecole_id=ecole_id,
        date_assignation=datetime(2026, 9, 1, 8),
    ))
    db.session.commit()
    objets = [admin, utilisateur_prof, gestionnaire_global, annee, professeur, classe, cours, inscription, pointage, fiche, journal]
    affectations_originales = sorted(db.session.execute(gestion_ecole.select()).all())
    prof_classes_originales = db.session.execute(professeur_classes.select()).all()
    chemin = scripts.create_school_backup(ecole_id)
    snapshot = json.loads(Path(chemin).read_text(encoding="utf-8"))
    assert snapshot["data"]["pointages_personnel"] == [scripts._serialize_instance(pointage)]
    assert snapshot["data"]["fiches_paie_personnel"] == [scripts._serialize_instance(fiche)]
    if ancienne_sauvegarde:
        def ancien_format(data):
            data.pop("pointages_personnel")
            data.pop("fiches_paie_personnel")
            data.pop("gestion_ecole")

        _reecrire_snapshot(chemin, ancien_format)
        pointage.motif = "Pointage actuel absent de l'ancienne sauvegarde"
        fiche.note = "Paiement actuel a conserver"
        db.session.commit()
    originaux = [(type(objet), scripts._serialize_instance(objet)) for objet in objets]
    db.session.expunge_all()
    assert scripts.restore_school_backup(Path(chemin).name, target_ecole_id=ecole_id)
    db.session.expire_all()
    for modele, original in originaux:
        assert scripts._serialize_instance(db.session.get(modele, original["id"])) == original
    assert sorted(db.session.execute(gestion_ecole.select()).all()) == affectations_originales
    assert db.session.execute(professeur_classes.select()).all() == prof_classes_originales
    _verifier_certificats_restaures(backup_app, certificats_ecole)


def test_backup_postgresql_pg_dump_sans_commande_sqlite(backup_app, monkeypatch):
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        url=make_url("postgresql+psycopg://test_user:test_password@127.0.0.1:5432/test_klasora"),
        raw_connection=Mock(side_effect=AssertionError("PostgreSQL ne doit pas ouvrir de connexion SQLite")),
    )
    session = SimpleNamespace(
        execute=Mock(side_effect=AssertionError("pg_dump ne doit executer aucun PRAGMA")),
        commit=Mock(side_effect=AssertionError("pg_dump ne doit committer aucune session metier")),
    )
    monkeypatch.setattr(scripts, "db", SimpleNamespace(engine=engine, session=session))
    monkeypatch.setattr(scripts.shutil, "which", lambda outil: "pg_dump" if outil == "pg_dump" else None)
    sqlite_interdit = Mock(side_effect=AssertionError("Aucun sqlite3.connect en PostgreSQL"))
    monkeypatch.setattr(scripts.sqlite3, "connect", sqlite_interdit)
    commandes = []

    def pg_dump_simule(command, **kwargs):
        commandes.append((command, kwargs))
        Path(command[command.index("--file") + 1]).write_bytes(b"PGDMP-test-dump")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(scripts.subprocess, "run", pg_dump_simule)
    sauvegarde = scripts.create_backup()
    assert sauvegarde.endswith(".dump")
    assert Path(sauvegarde).read_bytes() == b"PGDMP-test-dump"
    assert len(commandes) == 1
    commande, options = commandes[0]
    assert commande[0] == "pg_dump"
    assert options["shell"] is False
    assert options["env"]["PGPASSWORD"] == "test_password"
    assert "test_password" not in " ".join(commande)
    assert all("PRAGMA" not in argument.upper() for argument in commande)
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    engine.raw_connection.assert_not_called()
    sqlite_interdit.assert_not_called()
