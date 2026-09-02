from . import main
from .common import (
    TELEGRAM_CHAT_ID,
    datetime,
    envoyer_email,
    envoyer_telegram,
    jsonify,
    login_required,
    render_template,
    request,
    role_required,
)
from app.services import PER_PAGE_ALERTES, TELEGRAM_CHAT_ID, generer_alertes_automatiques, notifier_alertes


@main.route('/alertes')
@login_required
@role_required('admin')
def alertes():
    page = request.args.get('page', 1, type=int)
    all_alertes = generer_alertes_automatiques()
    total_alertes = len(all_alertes)
    total_pages = (total_alertes + PER_PAGE_ALERTES - 1) // PER_PAGE_ALERTES
    start = (page - 1) * PER_PAGE_ALERTES
    end = start + PER_PAGE_ALERTES
    alertes_page = all_alertes[start:end]

    # Notifications en arrière-plan
    notifier_alertes(alertes_page)

    stats = {
        "alertes_urgentes": sum(1 for a in all_alertes if a["type"] == "danger"),
        "alertes_importantes": sum(1 for a in all_alertes if a["type"] == "warning"),
        "alertes_info": sum(1 for a in all_alertes if a["type"] == "info"),
        "alertes_system": 0,
        "alertes_traitees": 0,
        "alertes_total": total_alertes
    }

    return render_template(
        'alertes.html',
        alertes=alertes_page,
        stats=stats,
        page=page,
        total_pages=total_pages
    )

@main.route('/api/alertes', methods=['GET'])
@login_required
def api_alertes():
    alertes = generer_alertes_automatiques(limit=50)  # limite pour performance
    for a in alertes:
        if isinstance(a['date'], datetime):
            a['date'] = a['date'].strftime('%d/%m/%Y %H:%M')
    return jsonify({'alertes': alertes})

@main.route('/api/alertes/<string:alert_id>/read', methods=['POST'])
@login_required
def marquer_alerte_lue(alert_id):
    return jsonify({'success': True, 'message': f'Alerte {alert_id} marquée comme lue'})

@main.route('/api/notifications/test', methods=['POST'])
@login_required
def envoyer_notification_test():
    try:
        data = request.get_json() or {}
        contact = data.get('contact', '').strip()
        message = data.get('message', 'Message de test depuis EduManage')
        channel = data.get('channel', 'app').lower()

        if channel not in ['app', 'telegram'] and not contact:
            return jsonify({'success': False, 'message': f'Contact requis pour le canal {channel}'}), 400

        if channel == 'app':
            return jsonify({'success': True, 'message': 'Notification ajoutée dans l’application'})

        if channel == 'email':
            ok = envoyer_email(contact, "Test de notification - EduManage", message)
            return jsonify({'success': ok, 'message': 'Email envoyé' if ok else 'Échec envoi email'})

        if channel == 'telegram':
            chat_id = contact if contact else TELEGRAM_CHAT_ID
            ok = envoyer_telegram(message, chat_id)
            return jsonify({'success': ok, 'message': 'Message Telegram envoyé' if ok else 'Échec envoi Telegram'})

        return jsonify({'success': False, 'message': 'Canal inconnu'}), 400

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
