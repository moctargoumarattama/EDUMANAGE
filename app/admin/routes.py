from flask import abort, render_template, redirect, url_for, flash, send_file, request, jsonify
from flask_login import current_user
from app.utils import get_ecole_filter_query
from . import admin_bp

# Import standard
from app import db
from sqlalchemy import inspect
from sqlalchemy.orm import joinedload
import os
import re
from datetime import datetime

from app.models import Log, Ecole, SupportTicket

# Formulaires
from app.admin.forms import BackupSchoolForm

# Fonctions scripts admin
from .scripts import (
    create_backup,
    restore_backup,
    clean_data,
    get_system_stats,
    delete_backup_file,
    download_backup_file,
    integrity_check,
    deploy_app,
    create_missing_tables,
    create_complete_backup,
    create_school_backup,
    inspect_school_backup,
    restore_school_backup,
    get_school_backups,
    init_annees_scolaires,
    get_maintenance_status,
    set_maintenance_status,
    get_auto_backup_config,
    set_auto_backup_config,
    get_cache_info,
    purge_cache,
    get_database_health,
    get_all_backups_list
)

BACKUP_DIR = 'backups'


@admin_bp.before_request
def require_super_admin_for_admin_blueprint():
    if request.endpoint == 'admin.static':
        return None
    if not getattr(current_user, 'is_authenticated', False):
        return redirect(url_for('main.login'))
    if getattr(current_user, 'role', None) != 'super_admin':
        abort(403)
    return None


# --- Dashboard (Redirige vers l'accueil Super Admin unifié) ---
@admin_bp.route('/admin')
def dashboard():
    return redirect(url_for('main.index'))

# --- Sauvegarde simple ---
@admin_bp.route('/admin/backup')
def backup():
    try:
        create_backup()
        flash("Sauvegarde effectuée avec succès !", "success")
    except Exception as e:
        flash(f"Erreur lors de la sauvegarde: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Restauration simple ---
@admin_bp.route('/admin/restore/<filename>')
def restore(filename):
    try:
        restore_backup(filename)
        flash(f"Restauration depuis {filename} réussie !", "success")
    except Exception as e:
        flash(f"Erreur lors de la restauration: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Nettoyage ---
@admin_bp.route('/admin/clean')
def clean():
    try:
        result = clean_data()
        flash(f"Nettoyage terminé: {result}", "success")
    except Exception as e:
        flash(f"Erreur lors du nettoyage: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Redirection de l'ancienne page backup vers le Hub unique ---
@admin_bp.route('/admin/backup_page')
def backup_page():
    return redirect(url_for('admin.maintenance_page'))

# --- Hub Unique de Maintenance & Opérations ---
@admin_bp.route('/admin/maintenance')
@admin_bp.route('/admin/maintenance_page')
def maintenance_page():
    """Page unique et centralisée pour la maintenance, les sauvegardes, la BDD et le cache"""
    maint_status = get_maintenance_status()
    auto_backup = get_auto_backup_config()
    backups = get_all_backups_list()
    db_health = get_database_health()
    cache_info = get_cache_info()

    return render_template(
        'maintenance.html',
        maint_status=maint_status,
        auto_backup=auto_backup,
        backups=backups,
        db_health=db_health,
        cache_info=cache_info
    )

# --- Bascule du Mode Maintenance ---
@admin_bp.route('/admin/maintenance/toggle', methods=['POST'])
def maintenance_toggle():
    """Active ou désactive le mode maintenance global avec message personnalisable"""
    try:
        active = request.form.get('active') == '1'
        message = request.form.get('message', '').strip()
        set_maintenance_status(active, message if message else None)
        flash(f"Mode maintenance {'activé' if active else 'désactivé'} avec succès !", "success")
    except Exception as e:
        flash(f"Erreur lors du changement de mode maintenance: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Configuration de la Sauvegarde Automatique Quotidienne ---
@admin_bp.route('/admin/backup/auto-config', methods=['POST'])
def auto_backup_config():
    """Configure la sauvegarde quotidienne automatique"""
    try:
        enabled = request.form.get('enabled') == '1'
        time_val = request.form.get('time', '02:00').strip()
        set_auto_backup_config(enabled, time_val)
        flash("Configuration de la sauvegarde automatique quotidienne enregistrée !", "success")
    except Exception as e:
        flash(f"Erreur configuration sauvegarde: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Purge du Cache Système ---
@admin_bp.route('/admin/cache/purge', methods=['POST'])
def purge_cache_route():
    """Purge le cache système (QR codes et temporaires)"""
    try:
        res = purge_cache()
        flash(f"Cache purgé avec succès : {res['deleted']} fichiers supprimés ({res['freed_kb']} Ko libérés)", "success")
    except Exception as e:
        flash(f"Erreur lors de la purge du cache: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Gestion des sauvegardes ---
@admin_bp.route('/admin/delete_backup/<filename>')
def delete_backup(filename):
    try:
        delete_backup_file(filename)
        flash(f"Sauvegarde {filename} supprimée avec succès !", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

@admin_bp.route('/admin/download_backup/<filename>')
def download_backup(filename):
    try:
        return download_backup_file(filename)
    except Exception as e:
        flash(f"Erreur lors du téléchargement: {str(e)}", "error")
        return redirect(url_for('admin.maintenance_page'))

# --- Vérification d'intégrité ---
@admin_bp.route('/admin/integrity_check')
def integrity_check_route():
    """Route pour la vérification d'intégrité"""
    try:
        results, summary = integrity_check()
        flash("Vérification d'intégrité terminée !", "success")
    except Exception as e:
        flash(f"Erreur lors de la vérification : {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))


# --- Logs ---
@admin_bp.route('/admin/logs')
def view_logs():
    """Affiche les logs système avec filtres, recherche et statistiques"""
    page = request.args.get('page', 1, type=int)
    level = request.args.get('level', '').strip()
    search = request.args.get('search', '').strip()
    module = request.args.get('module', '').strip()
    per_page = 40

    query = Log.query

    if level:
        query = query.filter(Log.level == level)
    if module:
        query = query.filter(Log.module == module)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                Log.action.ilike(search_pattern),
                Log.details.ilike(search_pattern),
                Log.module.ilike(search_pattern),
                Log.ip_address.ilike(search_pattern)
            )
        )

    logs = query.order_by(Log.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)

    # Statistiques globales
    total_logs = Log.query.count()
    total_info = Log.query.filter_by(level='INFO').count()
    total_warning = Log.query.filter_by(level='WARNING').count()
    total_error = Log.query.filter_by(level='ERROR').count()

    # Liste des modules existants pour la liste déroulante
    try:
        raw_modules = db.session.query(Log.module).distinct().filter(Log.module.isnot(None)).all()
        modules = sorted([m[0] for m in raw_modules if m[0]])
    except Exception:
        modules = []

    return render_template(
        'logs.html',
        logs=logs,
        total_logs=total_logs,
        total_info=total_info,
        total_warning=total_warning,
        total_error=total_error,
        modules=modules,
        current_level=level,
        current_search=search,
        current_module=module
    )

# --- Déploiement ---
@admin_bp.route('/admin/deploy', methods=['POST'])
def deploy():
    """Lance le déploiement de l'application"""
    result = deploy_app()
    
    if "Erreur" in result or "Exception" in result:
        flash(f"Erreur lors du déploiement: {result}", "error")
    else:
        flash(f"Déploiement réussi: {result}", "success")
    
    return redirect(url_for('main.index'))


# --- Création des tables manquantes ---
@admin_bp.route('/admin/create_tables')
def create_tables():
    """Crée les tables manquantes"""
    try:
        result = create_missing_tables()
        if result:
            flash("Tables manquantes créées avec succès!", "success")
        else:
            flash("Erreur lors de la création des tables", "error")
    except Exception as e:
        flash(f"Erreur lors de la création des tables: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Initialisation des années scolaires ---
@admin_bp.route('/admin/init_annees')
def init_annees():
    """Initialise les années scolaires"""
    try:
        result = init_annees_scolaires()
        flash(result, "success")
    except Exception as e:
        flash(f"Erreur lors de l'initialisation: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Sauvegarde complète ---
@admin_bp.route('/admin/backup_complete')
def backup_complete():
    """Sauvegarde complète de toutes les écoles"""
    try:
        filename = create_complete_backup()
        flash(f"Sauvegarde complète créée: {filename}", "success")
    except Exception as e:
        flash(f"Erreur lors de la sauvegarde complète: {str(e)}", "error")
    return redirect(url_for('admin.backup_page'))

# --- Sauvegarde par école ---
@admin_bp.route('/admin/backup_school', methods=['GET', 'POST'])
def backup_school():
    form = BackupSchoolForm()
    
    # Remplir les choix d'écoles
    ecoles = get_ecole_filter_query(Ecole).all()
    form.ecole_id.choices = [(str(ecole.id), ecole.nom) for ecole in ecoles]
    
    if form.validate_on_submit():
        ecole_id = form.ecole_id.data
        try:
            filename = create_school_backup(ecole_id)
            flash(f"Sauvegarde pour l'école créée: {filename}", "success")
        except Exception as e:
            flash(f"Erreur lors de la sauvegarde: {str(e)}", "error")
        return redirect(url_for('admin.backup_page'))
    
    return render_template(
        'backup_school.html',
        form=form,
        ecoles=ecoles,
        get_school_backups=get_school_backups
    )

# --- Restauration par école avec confirmation forte ---
@admin_bp.route('/admin/restore_school/<filename>', methods=['GET', 'POST'])
def restore_school(filename):
    """Restauration d'une sauvegarde d'école spécifique avec prévisualisation et confirmation forte"""
    if request.method == 'POST':
        confirmation = request.form.get('confirmation_code', '').strip()
        target_ecole_id = request.form.get('ecole_id', type=int)
        try:
            restore_school_backup(filename, target_ecole_id=target_ecole_id, confirmation_code=confirmation)
            flash(f"Restauration réussie depuis {filename} !", "success")
        except Exception as e:
            flash(f"Erreur lors de la restauration: {str(e)}", "danger")
        return redirect(url_for('admin.maintenance_page'))

    # GET: Prévisualisation
    try:
        metadata, data = inspect_school_backup(filename)
        return render_template('admin/preview_restore.html', filename=filename, metadata=metadata, counts=metadata.get('counts', {}))
    except Exception as e:
        flash(f"Erreur lors de l'inspection de la sauvegarde : {str(e)}", "danger")
        return redirect(url_for('admin.maintenance_page'))


# --- Support Technique KLASORA (Super Admin) ---

@admin_bp.route('/admin/support')
def support_tickets():
    """
    Gestion des tickets de support technique KLASORA pour le Super Admin.
    Filtrage par statut ('tous', 'nouveau', 'en_cours', 'resolu'), école, et recherche texte.
    """
    statut_filter = request.args.get('statut', 'tous').strip()
    ecole_filter = request.args.get('ecole_id', '').strip()
    search = request.args.get('q', '').strip()

    query = SupportTicket.query.options(
        joinedload(SupportTicket.ecole),
        joinedload(SupportTicket.utilisateur)
    )

    if statut_filter and statut_filter in ('nouveau', 'en_cours', 'resolu'):
        query = query.filter(SupportTicket.statut == statut_filter)

    if ecole_filter and ecole_filter.isdigit():
        query = query.filter(SupportTicket.ecole_id == int(ecole_filter))

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            db.or_(
                SupportTicket.sujet.ilike(search_term),
                SupportTicket.message.ilike(search_term),
                SupportTicket.page_url.ilike(search_term)
            )
        )

    tickets = query.order_by(SupportTicket.created_at.desc()).all()

    # Compteurs globaux pour les KPI cards
    total_tickets = SupportTicket.query.count()
    nouveau_count = SupportTicket.query.filter_by(statut='nouveau').count()
    en_cours_count = SupportTicket.query.filter_by(statut='en_cours').count()
    resolu_count = SupportTicket.query.filter_by(statut='resolu').count()

    ecoles = Ecole.query.order_by(Ecole.nom.asc()).all()

    return render_template(
        'support.html',
        tickets=tickets,
        total_tickets=total_tickets,
        nouveau_count=nouveau_count,
        en_cours_count=en_cours_count,
        resolu_count=resolu_count,
        ecoles=ecoles,
        current_statut=statut_filter,
        current_ecole_id=ecole_filter,
        current_search=search
    )


@admin_bp.route('/admin/support/<int:ticket_id>')
def support_ticket_detail(ticket_id):
    """
    Détail d'un ticket de support (retourne JSON pour affichage modal ou vue détaillée).
    """
    ticket = SupportTicket.query.get_or_404(ticket_id)

    user_nom = f"{ticket.utilisateur.nom} {ticket.utilisateur.prenom or ''}".strip() if ticket.utilisateur else "Utilisateur inconnu"
    user_email = ticket.utilisateur.email if ticket.utilisateur else ""
    user_tel = ticket.utilisateur.telephone if ticket.utilisateur else ""
    ecole_nom = ticket.ecole.nom if ticket.ecole else "École inconnue"
    ecole_tel = ticket.ecole.telephone if ticket.ecole else ""

    # Téléphone à privilégier pour WhatsApp
    tel_contact = user_tel or ecole_tel or ""
    tel_clean = re.sub(r'[^0-9]', '', tel_contact)

    data = {
        'id': ticket.id,
        'sujet': ticket.sujet,
        'message': ticket.message,
        'statut': ticket.statut,
        'role': ticket.role,
        'page_url': ticket.page_url,
        'user_agent': ticket.user_agent,
        'created_at': ticket.created_at.strftime('%d/%m/%Y à %H:%M') if ticket.created_at else "",
        'updated_at': ticket.updated_at.strftime('%d/%m/%Y à %H:%M') if ticket.updated_at else "",
        'resolved_at': ticket.resolved_at.strftime('%d/%m/%Y à %H:%M') if ticket.resolved_at else None,
        'user': {
            'id': ticket.utilisateur_id,
            'nom_complet': user_nom,
            'email': user_email,
            'telephone': user_tel
        },
        'ecole': {
            'id': ticket.ecole_id,
            'nom': ecole_nom,
            'telephone': ecole_tel
        },
        'whatsapp_tel': tel_clean
    }

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('json'):
        return jsonify({'success': True, 'ticket': data})

    return render_template('support.html', ticket=ticket, data=data)


@admin_bp.route('/admin/support/<int:ticket_id>/statut', methods=['POST'])
def support_ticket_update_statut(ticket_id):
    """
    Mise à jour du statut d'un ticket ('nouveau', 'en_cours', 'resolu').
    """
    ticket = SupportTicket.query.get_or_404(ticket_id)

    if request.is_json:
        payload = request.get_json() or {}
        nouveau_statut = payload.get('statut')
    else:
        nouveau_statut = request.form.get('statut')

    if nouveau_statut not in ('nouveau', 'en_cours', 'resolu'):
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Statut invalide.'}), 400
        flash("Statut invalide.", "error")
        return redirect(url_for('admin.support_tickets'))

    ticket.statut = nouveau_statut
    ticket.updated_at = datetime.utcnow()
    if nouveau_statut == 'resolu':
        ticket.resolved_at = datetime.utcnow()
    else:
        ticket.resolved_at = None

    try:
        db.session.commit()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'message': f'Statut du ticket mis à jour vers "{nouveau_statut}".',
                'statut': ticket.statut,
                'resolved_at': ticket.resolved_at.strftime('%d/%m/%Y à %H:%M') if ticket.resolved_at else None
            })
        flash(f"Statut du ticket #{ticket.id} mis à jour : {nouveau_statut}.", "success")
    except Exception as e:
        db.session.rollback()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': "Erreur lors de la mise à jour."}), 500
        flash(f"Erreur lors de la mise à jour : {str(e)}", "error")

    return redirect(url_for('admin.support_tickets'))
