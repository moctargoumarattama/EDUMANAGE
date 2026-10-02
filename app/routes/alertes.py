from . import main
from .common import (
    Classe,
    Eleve,
    current_app,
    current_user,
    datetime,
    flash,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    session,
    url_for,
)
from app.authorization import can_access_class
from app.services import generer_alertes_automatiques, notifier_alertes
from app.services.annees_scolaires import get_annee_consultee
from app.utils_classes import classes_triees_pedagogique


@main.route('/alertes')
@login_required
@role_required('admin', 'professeur', 'parent')
def alertes():
    """Redirection transparente 302 vers l'accueil avec ouverture automatique du modal d'alertes."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return redirect(url_for('main.api_alertes'))
    return redirect(url_for('main.index', open_alertes=1))


@main.route('/api/alertes', methods=['GET'])
@login_required
@role_required('admin', 'professeur', 'parent')
def api_alertes():
    ecole_id = getattr(current_user, 'ecole_id', None)
    if not ecole_id:
        return jsonify({'alertes': [], 'stats': {}})

    annee = get_annee_consultee(ecole_id)
    if not annee:
        return jsonify({'alertes': [], 'stats': {}})

    limit_arg = request.args.get('limit', type=int)
    alertes = generer_alertes_automatiques(ecole_id=ecole_id, annee=annee, limit=limit_arg)

    # Filtrage selon le rôle
    if current_user.role == 'professeur':
        classes_prof = [c for c in Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).all() if can_access_class(c)]
        classes_ids = {c.id for c in classes_prof}
        alertes = [a for a in alertes if a.get('classe_id') in classes_ids]
    elif current_user.role == 'parent':
        mes_eleves_ids = {e.id for e in Eleve.query.filter_by(parent_id=current_user.id, ecole_id=ecole_id).all()}
        alertes = [a for a in alertes if a.get('eleve_id') in mes_eleves_ids]

    alertes_traitees_ids = set(session.get('alertes_traitees', []))
    for a in alertes:
        a['traitee'] = a['id'] in alertes_traitees_ids
        if isinstance(a.get('date'), datetime):
            a['date'] = a['date'].strftime('%d/%m/%Y %H:%M')

    alertes_actives = [a for a in alertes if not a.get('traitee')]
    stats = {
        'total': len(alertes),
        'actives': len(alertes_actives),
        'traitees': len(alertes) - len(alertes_actives),
        'paiements': sum(1 for a in alertes_actives if a.get('source') == 'Paiements'),
        'jamais_paye': sum(1 for a in alertes_actives if a.get('source') == 'Paiements' and a.get('jamais_paye')),
        'absences': sum(1 for a in alertes_actives if a.get('source') == 'Absences'),
        'notes': sum(1 for a in alertes_actives if a.get('source') == 'Notes'),
        'urgentes': sum(1 for a in alertes_actives if a.get('type') == 'danger'),
    }
    return jsonify({'alertes': alertes, 'stats': stats})


@main.route('/api/alertes/count', methods=['GET'])
@login_required
@role_required('admin', 'professeur', 'parent')
def api_alertes_count():
    ecole_id = getattr(current_user, 'ecole_id', None)
    if not ecole_id:
        return jsonify({'count': 0, 'actives': 0, 'total': 0, 'jamais_paye': 0, 'paiements': 0, 'absences': 0, 'notes': 0, 'urgentes': 0})

    annee = get_annee_consultee(ecole_id)
    if not annee:
        return jsonify({'count': 0, 'actives': 0, 'total': 0, 'jamais_paye': 0, 'paiements': 0, 'absences': 0, 'notes': 0, 'urgentes': 0})

    alertes = generer_alertes_automatiques(ecole_id=ecole_id, annee=annee)

    if current_user.role == 'professeur':
        classes_prof = [c for c in Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).all() if can_access_class(c)]
        classes_ids = {c.id for c in classes_prof}
        alertes = [a for a in alertes if a.get('classe_id') in classes_ids]
    elif current_user.role == 'parent':
        mes_eleves_ids = {e.id for e in Eleve.query.filter_by(parent_id=current_user.id, ecole_id=ecole_id).all()}
        alertes = [a for a in alertes if a.get('eleve_id') in mes_eleves_ids]

    alertes_traitees_ids = set(session.get('alertes_traitees', []))
    alertes_actives = [a for a in alertes if a['id'] not in alertes_traitees_ids]
    return jsonify({
        'count': len(alertes_actives),
        'actives': len(alertes_actives),
        'total': len(alertes),
        'traitees': len(alertes) - len(alertes_actives),
        'jamais_paye': sum(1 for a in alertes_actives if a.get('source') == 'Paiements' and a.get('jamais_paye')),
        'paiements': sum(1 for a in alertes_actives if a.get('source') == 'Paiements'),
        'absences': sum(1 for a in alertes_actives if a.get('source') == 'Absences'),
        'notes': sum(1 for a in alertes_actives if a.get('source') == 'Notes'),
        'urgentes': sum(1 for a in alertes_actives if a.get('type') == 'danger'),
    })


@main.route('/api/alertes/<string:alert_id>/read', methods=['POST'])
@login_required
@role_required('admin', 'professeur', 'parent')
def marquer_alerte_lue(alert_id):
    raw_traitees = session.get('alertes_traitees', [])
    traitees_set = set(raw_traitees)
    traitees_list = list(raw_traitees)
    data = request.get_json(silent=True) or {}
    action = data.get('action', 'toggle')

    ecole_id = getattr(current_user, 'ecole_id', None)
    annee = get_annee_consultee(ecole_id) if ecole_id else None

    if alert_id == 'all':
        if ecole_id and annee:
            all_alertes = generer_alertes_automatiques(ecole_id=ecole_id, annee=annee)
            if current_user.role == 'professeur':
                classes_prof = [c for c in Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id).all() if can_access_class(c)]
                classes_ids = {c.id for c in classes_prof}
                all_alertes = [a for a in all_alertes if a.get('classe_id') in classes_ids]
            elif current_user.role == 'parent':
                mes_eleves_ids = {e.id for e in Eleve.query.filter_by(parent_id=current_user.id, ecole_id=ecole_id).all()}
                all_alertes = [a for a in all_alertes if a.get('eleve_id') in mes_eleves_ids]
            for a in all_alertes:
                aid = a['id']
                if aid not in traitees_set:
                    traitees_set.add(aid)
                    traitees_list.append(aid)
        msg = "Toutes les alertes ont été marquées comme traitées"
        is_traitee = True
    elif action == 'untreat' or (action == 'toggle' and alert_id in traitees_set):
        traitees_set.discard(alert_id)
        traitees_list = [x for x in traitees_list if x != alert_id]
        msg = "Alerte réactivée"
        is_traitee = False
    else:
        traitees_set.add(alert_id)
        traitees_list = [x for x in traitees_list if x != alert_id]
        traitees_list.append(alert_id)
        msg = "Alerte marquée comme traitée"
        is_traitee = True

    session['alertes_traitees'] = traitees_list[-100:]
    session.modified = True
    return jsonify({
        'success': True,
        'message': msg,
        'traitees_count': len(traitees_set),
        'is_traitee': is_traitee,
        'alert_id': alert_id
    })


@main.route('/api/notifications/test', methods=['POST'])
@login_required
@role_required('admin')
def envoyer_notification_test():
    try:
        data = request.get_json() or {}
        contact = data.get('contact', '').strip()
        message = data.get('message', 'Message de test depuis KLASORA')
        channel = data.get('channel', 'app').lower()

        if channel not in ['app'] and not contact:
            return jsonify({'success': False, 'message': f'Contact requis pour le canal {channel}'}), 400

        if channel == 'app':
            return jsonify({'success': True, 'message': 'Notification ajoutée dans l’application'})

        if channel == 'email':
            return jsonify({
                'success': False,
                'message': "Canal email desactive. Utilisez WhatsApp ou les notifications applicatives."
            }), 400

        return jsonify({'success': False, 'message': 'Canal inconnu'}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
