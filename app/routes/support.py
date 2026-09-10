import re
from datetime import datetime, timedelta
from flask import request, jsonify, session
from flask_login import login_required, current_user

from app import db
from app.models import SupportTicket
from . import main


@main.route('/api/support/ticket', methods=['POST'])
@login_required
def creer_support_ticket():
    """
    Création d'un ticket de support technique KLASORA.
    Accessible UNIQUEMENT aux rôles : admin, professeur, parent.
    Le Super Admin (technicien plateforme) se voit refuser l'accès avec HTTP 403.
    Le contexte (ecole_id, user_id, role) est exclusivement résolu côté serveur.
    """
    # 1. Contrôle strict du rôle (Super Admin refusé, autres rôles non reconnus refusés)
    user_role = getattr(current_user, 'role', None)
    if user_role == 'super_admin':
        return jsonify({
            'success': False,
            'message': "Les administrateurs plateforme (Super Admin) ne peuvent pas créer de ticket support."
        }), 403

    if user_role not in ('admin', 'professeur', 'parent'):
        return jsonify({
            'success': False,
            'message': "Accès non autorisé pour ce type d'utilisateur."
        }), 403

    # 2. Résolution stricte de l'école (obligatoire pour chaque ticket)
    ecole_id = getattr(current_user, 'ecole_id', None)
    if not ecole_id:
        ecole_id = session.get('ecole_id')

    if not ecole_id:
        return jsonify({
            'success': False,
            'message': "Aucun établissement scolaire valide n'est rattaché à cette session."
        }), 400

    # 3. Récupération des données du formulaire ou payload JSON
    if request.is_json:
        data = request.get_json() or {}
        sujet = (data.get('sujet') or '').strip()
        message = (data.get('message') or '').strip()
        page_url = (data.get('page_url') or '').strip()
    else:
        sujet = (request.form.get('sujet') or '').strip()
        message = (request.form.get('message') or '').strip()
        page_url = (request.form.get('page_url') or '').strip()

    # 4. Validation des entrées
    if not sujet:
        return jsonify({
            'success': False,
            'message': "Veuillez indiquer le sujet de votre problème."
        }), 400

    if len(sujet) < 3 or len(sujet) > 150:
        return jsonify({
            'success': False,
            'message': "Le sujet doit comporter entre 3 et 150 caractères."
        }), 400

    if not message:
        return jsonify({
            'success': False,
            'message': "Veuillez décrire votre problème."
        }), 400

    if len(message) < 10:
        return jsonify({
            'success': False,
            'message': "Veuillez donner une description d'au moins 10 caractères."
        }), 400

    if len(message) > 4000:
        return jsonify({
            'success': False,
            'message': "La description ne doit pas dépasser 4000 caractères."
        }), 400

    # Sécurisation de la page URL (tronquée si trop longue)
    if page_url:
        page_url = page_url[:255]
    else:
        page_url = (request.referrer or '')[:255]

    user_agent = (request.user_agent.string if request.user_agent else '')[:255]

    # 5. Protection anti-spam / Rate limiting (5 tickets par 10 minutes max par utilisateur)
    dix_minutes_avant = datetime.utcnow() - timedelta(minutes=10)
    tickets_recents = SupportTicket.query.filter(
        SupportTicket.utilisateur_id == current_user.id,
        SupportTicket.created_at >= dix_minutes_avant
    ).count()

    if tickets_recents >= 5:
        return jsonify({
            'success': False,
            'message': "Trop de demandes envoyées récemment. Veuillez patienter quelques minutes avant d'en créer une nouvelle."
        }), 429

    # 6. Création et enregistrement du ticket
    try:
        nouveau_ticket = SupportTicket(
            ecole_id=ecole_id,
            utilisateur_id=current_user.id,
            role=user_role,
            sujet=sujet,
            message=message,
            page_url=page_url,
            statut='nouveau',
            user_agent=user_agent
        )
        db.session.add(nouveau_ticket)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': "Votre demande a été envoyée au support KLASORA.",
            'ticket_id': nouveau_ticket.id
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': "Une erreur est survenue lors de l'enregistrement de votre demande. Veuillez réessayer."
        }), 500
