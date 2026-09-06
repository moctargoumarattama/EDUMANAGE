from . import main
from .common import (
    Classe,
    current_user,
    datetime,
    envoyer_email,
    jsonify,
    login_required,
    render_template,
    request,
    role_required,
    session,
)
from app.services import PER_PAGE_ALERTES, generer_alertes_automatiques, notifier_alertes


@main.route('/alertes')
@login_required
@role_required('admin')
def alertes():
    # Classes de l'école active
    classes = Classe.query.filter_by(ecole_id=current_user.ecole_id).order_by(Classe.nom.asc()).all()

    # Suivi des alertes traitées via session
    alertes_traitees_ids = set(session.get('alertes_traitees', []))

    all_alertes = generer_alertes_automatiques()
    for a in all_alertes:
        a['traitee'] = a['id'] in alertes_traitees_ids

    total_alertes = len(all_alertes)
    alertes_actives = [a for a in all_alertes if not a['traitee']]
    alertes_traitees_count = len(all_alertes) - len(alertes_actives)
    montant_retard = sum(a.get("details", {}).get("montant_total_du", 0) for a in alertes_actives if a.get("source") == "Paiements")

    stats = {
        "alertes_urgentes": sum(1 for a in alertes_actives if a["type"] == "danger"),
        "alertes_importantes": sum(1 for a in alertes_actives if a["type"] == "warning"),
        "alertes_info": sum(1 for a in alertes_actives if a["type"] == "info"),
        "alertes_system": 0,
        "alertes_traitees": alertes_traitees_count,
        "alertes_total": total_alertes,
        "alertes_actives": len(alertes_actives),
        "total_paiements": sum(1 for a in alertes_actives if a.get("source") == "Paiements"),
        "total_absences": sum(1 for a in alertes_actives if a.get("source") == "Absences"),
        "total_notes": sum(1 for a in alertes_actives if a.get("source") == "Notes"),
        "montant_retard_total": montant_retard,
        "montant_retard_total_str": f"{montant_retard:,.0f}".replace(",", " ") + " FCFA"
    }

    # Structuration des alertes par classe
    classes_dict = {}
    for c in classes:
        classes_dict[c.id] = {
            'classe': c,
            'id': c.id,
            'nom': c.nom,
            'niveau': getattr(c, 'niveau', '') or '',
            'salle': getattr(c, 'salle', '') or '',
            'effectif': getattr(c, 'effectif', 0) or 0,
            'alertes': [],
            'nb_actives': 0,
            'nb_traitees': 0,
            'nb_paiements': 0,
            'nb_absences': 0,
            'nb_notes': 0,
            'has_danger': False,
            'has_warning': False,
        }

    sans_classe_group = {
        'classe': None,
        'id': 'sans-classe',
        'nom': 'Élèves non assignés / Sans classe',
        'niveau': '',
        'salle': '',
        'effectif': 0,
        'alertes': [],
        'nb_actives': 0,
        'nb_traitees': 0,
        'nb_paiements': 0,
        'nb_absences': 0,
        'nb_notes': 0,
        'has_danger': False,
        'has_warning': False,
    }

    for a in all_alertes:
        cid = a.get('classe_id')
        grp = classes_dict.get(cid, sans_classe_group)
        grp['alertes'].append(a)
        if not a['traitee']:
            grp['nb_actives'] += 1
            if a['type'] == 'danger':
                grp['has_danger'] = True
            elif a['type'] == 'warning':
                grp['has_warning'] = True
            if a.get('source') == 'Paiements':
                grp['nb_paiements'] += 1
            elif a.get('source') == 'Absences':
                grp['nb_absences'] += 1
            elif a.get('source') == 'Notes':
                grp['nb_notes'] += 1
        else:
            grp['nb_traitees'] += 1

    classes_alertes = list(classes_dict.values())
    if sans_classe_group['alertes']:
        classes_alertes.append(sans_classe_group)

    # Notifications en arrière-plan pour les alertes urgentes non traitées
    notifier_alertes([a for a in alertes_actives if a["type"] in ["danger", "warning"]])

    return render_template(
        'alertes.html',
        alertes=all_alertes,
        classes=classes,
        classes_alertes=classes_alertes,
        stats=stats
    )


@main.route('/api/alertes', methods=['GET'])
@login_required
def api_alertes():
    alertes = generer_alertes_automatiques(limit=50)
    alertes_traitees_ids = set(session.get('alertes_traitees', []))
    for a in alertes:
        a['traitee'] = a['id'] in alertes_traitees_ids
        if isinstance(a['date'], datetime):
            a['date'] = a['date'].strftime('%d/%m/%Y %H:%M')
    return jsonify({'alertes': alertes})


@main.route('/api/alertes/<string:alert_id>/read', methods=['POST'])
@login_required
def marquer_alerte_lue(alert_id):
    traitees = set(session.get('alertes_traitees', []))
    data = request.get_json(silent=True) or {}
    action = data.get('action', 'toggle')

    if alert_id == 'all':
        all_alertes = generer_alertes_automatiques()
        for a in all_alertes:
            traitees.add(a['id'])
        msg = "Toutes les alertes ont été marquées comme traitées"
        is_traitee = True
    elif action == 'untreat' or (action == 'toggle' and alert_id in traitees):
        traitees.discard(alert_id)
        msg = "Alerte réactivée"
        is_traitee = False
    else:
        traitees.add(alert_id)
        msg = "Alerte marquée comme traitée"
        is_traitee = True

    session['alertes_traitees'] = list(traitees)
    session.modified = True
    return jsonify({
        'success': True,
        'message': msg,
        'traitees_count': len(traitees),
        'is_traitee': is_traitee,
        'alert_id': alert_id
    })


@main.route('/api/notifications/test', methods=['POST'])
@login_required
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
            ok = envoyer_email(contact, "Test de notification - KLASORA", message)
            return jsonify({'success': ok, 'message': 'Email envoyé' if ok else 'Échec envoi email'})

        return jsonify({'success': False, 'message': 'Canal inconnu'}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
