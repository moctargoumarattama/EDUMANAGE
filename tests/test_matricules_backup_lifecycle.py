"""Identification permanente lors des sauvegardes, restaurations et suppressions."""
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app import create_app, db
from app.config import Config
from app.models import Ecole, Eleve, MatriculeSequence
from app.admin import scripts
from app.routes.ecoles import safe_delete_ecole
from app.services.matricule_service import generer_prochain_matricule


class BackupTestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


@pytest.fixture
def ecole_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(scripts, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(scripts, "log_action", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.utils.nettoyer_repertoire_ecole", lambda *args, **kwargs: None)
    app = create_app(BackupTestConfig)
    with app.app_context():
        db.create_all()
        ecole = Ecole(nom="Ecole Sauvegarde", onboarding_complete=True)
        db.session.add(ecole)
        db.session.flush()
        eleve = Eleve(
            nom="Sow", prenom="Awa", date_naissance=date(2014, 1, 1),
            date_inscription=datetime(2026, 9, 1), annee_premiere_ecole=2026,
            ecole_id=ecole.id,
        )
        db.session.add(eleve)
        db.session.commit()
        ids = (ecole.id, eleve.id, eleve.matricule)
        db.session.expunge_all()
        yield ids
        db.session.remove()
        db.drop_all()


def _modifier_snapshot(path, modifier):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    modifier(snapshot["data"])
    snapshot["metadata"]["checksum"] = scripts._compute_backup_checksum(snapshot["data"])
    Path(path).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return snapshot


def test_backup_recent_restaure_matricule_et_compteur(ecole_backup):
    ecole_id, eleve_id, matricule = ecole_backup
    path = scripts.create_school_backup(ecole_id)
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    assert snapshot["data"]["eleves"][0]["matricule"] == matricule
    assert snapshot["data"]["matricule_sequences"][0]["dernier_numero"] == 1
    assert scripts.restore_school_backup(Path(path).name, target_ecole_id=ecole_id)
    assert db.session.get(Eleve, eleve_id).matricule == matricule
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0002"
    db.session.rollback()


def test_ancien_backup_sans_matricule_ni_compteur_garde_matricule_actuel(ecole_backup):
    ecole_id, eleve_id, matricule = ecole_backup
    path = scripts.create_school_backup(ecole_id)

    def ancien_format(data):
        data.pop("matricule_sequences")
        for eleve in data["eleves"]:
            eleve.pop("matricule")

    snapshot = _modifier_snapshot(path, ancien_format)
    assert scripts.restore_school_backup(Path(path).name, target_ecole_id=ecole_id)
    assert db.session.get(Eleve, eleve_id).matricule == matricule
    assert "matricule" not in snapshot["data"]["eleves"][0]
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0002"
    db.session.rollback()


def test_restaurer_ancien_compteur_ne_le_fait_pas_reculer(ecole_backup):
    ecole_id, eleve_id, matricule = ecole_backup
    path = scripts.create_school_backup(ecole_id)
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0002"
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0003"
    db.session.commit()
    assert scripts.restore_school_backup(Path(path).name, target_ecole_id=ecole_id)
    assert db.session.get(Eleve, eleve_id).matricule == matricule
    assert db.session.get(MatriculeSequence, (ecole_id, "26")).dernier_numero == 3
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0004"
    db.session.rollback()


def test_restore_importe_compteur_snapshot_plus_grand_et_prefixe_absent(ecole_backup):
    ecole_id, _, _ = ecole_backup
    path = scripts.create_school_backup(ecole_id)

    def compteurs_reserves(data):
        data["matricule_sequences"][0]["dernier_numero"] = 8
        data["matricule_sequences"].append({
            "ecole_id": ecole_id, "prefixe": "25", "dernier_numero": 4,
        })

    _modifier_snapshot(path, compteurs_reserves)
    assert scripts.restore_school_backup(Path(path).name, target_ecole_id=ecole_id)
    assert generer_prochain_matricule(ecole_id, 2026) == "26-0009"
    assert generer_prochain_matricule(ecole_id, 2025) == "25-0005"
    db.session.rollback()


def test_restore_refuse_matricule_divergent_avant_suppression(ecole_backup):
    ecole_id, eleve_id, matricule = ecole_backup
    path = scripts.create_school_backup(ecole_id)
    _modifier_snapshot(path, lambda data: data["eleves"][0].update(matricule="26-9999"))
    with pytest.raises(ValueError, match="permanent"):
        scripts.restore_school_backup(Path(path).name, target_ecole_id=ecole_id)
    assert db.session.get(Eleve, eleve_id).matricule == matricule
    assert Eleve.query.filter_by(ecole_id=ecole_id).count() == 1
    assert db.session.get(MatriculeSequence, (ecole_id, "26")).dernier_numero == 1


def test_suppression_ecole_nettoie_compteurs_sans_dependre_fk_sqlite(ecole_backup):
    ecole_id, _, _ = ecole_backup
    autre = Ecole(nom="Autre Ecole")
    db.session.add(autre)
    db.session.flush()
    autre_id = autre.id
    db.session.add(MatriculeSequence(ecole_id=autre_id, prefixe="26", dernier_numero=7))
    db.session.commit()
    assert safe_delete_ecole(ecole_id) == "Ecole Sauvegarde"
    assert db.session.get(Ecole, ecole_id) is None
    assert MatriculeSequence.query.filter_by(ecole_id=ecole_id).count() == 0
    assert db.session.get(MatriculeSequence, (autre_id, "26")).dernier_numero == 7
