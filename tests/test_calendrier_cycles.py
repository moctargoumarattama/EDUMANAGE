"""Une année primaire exige trois compositions ; un collège exige deux semestres."""

from datetime import date

from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import TestingConfig
from app.models import AnneeScolaire, Classe, Ecole, PeriodeBulletin
from app.services.semestres import calendrier_configure_pour_cycles, configurer_semestres_annee


class CalendrierCyclesTestConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool, 'connect_args': {'check_same_thread': False},
    }


def test_calendrier_primaire_et_mixte():
    app = create_app(CalendrierCyclesTestConfig)
    with app.app_context():
        assert str(db.engine.url) == 'sqlite:///:memory:'
        db.create_all()
        ecole = Ecole(nom='École de test')
        db.session.add(ecole)
        db.session.flush()
        annee = AnneeScolaire(nom='2026-2027', date_debut=date(2026, 9, 1),
                               date_fin=date(2027, 6, 30), statut='planifiee', ecole_id=ecole.id)
        db.session.add(annee)
        db.session.flush()
        db.session.add(Classe(nom='CM2 A', niveau='CM2', ecole_id=ecole.id,
                              annee_scolaire_id=annee.id))
        db.session.flush()
        assert calendrier_configure_pour_cycles(ecole.id, annee.id)[0] is False

        for nom, debut, fin in (
            ('1ère Composition', date(2026, 9, 1), date(2026, 12, 15)),
            ('2ème Composition', date(2026, 12, 16), date(2027, 3, 15)),
            ('3ème Composition', date(2027, 3, 16), date(2027, 6, 30)),
        ):
            db.session.add(PeriodeBulletin(nom=nom, date_debut=debut, date_fin=fin,
                                           ecole_id=ecole.id, annee_id=annee.id))
        db.session.flush()
        assert calendrier_configure_pour_cycles(ecole.id, annee.id)[0] is True

        db.session.add(Classe(nom='6e A', niveau='6e', ecole_id=ecole.id,
                              annee_scolaire_id=annee.id))
        db.session.flush()
        assert calendrier_configure_pour_cycles(ecole.id, annee.id)[0] is False
        _, erreur = configurer_semestres_annee(ecole.id, annee.id, date(2027, 1, 31))
        assert erreur is None
        assert calendrier_configure_pour_cycles(ecole.id, annee.id)[0] is True
        db.session.remove()
        db.drop_all()
