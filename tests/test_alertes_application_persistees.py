"""Le canal applicatif doit créer une alerte visible et conserver son état lu."""

from datetime import date

from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import TestingConfig
from app.models import Alerte, AnneeScolaire, Ecole, Utilisateur


class AlertesTestConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool, 'connect_args': {'check_same_thread': False},
    }


def test_notification_test_visible_et_lue_durablement():
    app = create_app(AlertesTestConfig)
    with app.app_context():
        assert str(db.engine.url) == 'sqlite:///:memory:'
        db.create_all()
        ecole = Ecole(nom='École de test', onboarding_complete=True)
        db.session.add(ecole)
        db.session.flush()
        annee = AnneeScolaire(nom='2026-2027', date_debut=date(2026, 9, 1),
                               date_fin=date(2027, 6, 30), statut='active', ecole_id=ecole.id)
        admin = Utilisateur(nom='Admin', prenom='Test', email='alertes@test.local',
                            mot_de_passe='secret', role='admin', ecole_id=ecole.id)
        db.session.add_all([annee, admin])
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(admin.id)
            session['_fresh'] = True
            session['ecole_id'] = ecole.id

        response = client.post('/api/notifications/test', json={'channel': 'app', 'message': 'Essai local'})
        assert response.status_code == 200
        alerte = Alerte.query.one()
        assert alerte.message == 'Essai local'
        alert_id = f'app-{alerte.id}'
        assert any(item['id'] == alert_id for item in client.get('/api/alertes').get_json()['alertes'])

        assert client.post(f'/api/alertes/{alert_id}/read', json={'action': 'treat'}).status_code == 200
        db.session.refresh(alerte)
        assert alerte.date_lue is not None
        assert next(item for item in client.get('/api/alertes').get_json()['alertes']
                    if item['id'] == alert_id)['traitee'] is True
        assert client.post(f'/api/alertes/{alert_id}/read', json={'action': 'untreat'}).status_code == 200
        assert alerte.date_lue is None
        assert client.post('/api/alertes/all/read', json={}).status_code == 200
        assert alerte.date_lue is not None
        db.session.remove()
        db.drop_all()
