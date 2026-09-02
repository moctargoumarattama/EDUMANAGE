from flask import render_template, redirect, url_for, flash, send_file, request, jsonify
from app.utils import get_ecole_filter_query
from . import admin_bp

# Import standard
from app import db
from sqlalchemy import inspect
import os
import re
from datetime import datetime

# Import modèles
from app.models import Log, Ecole

# Formulaires
from app.admin.forms import BackupSchoolForm

# Fonctions scripts admin
from .scripts import (
    create_backup,
    restore_backup,
    clean_data,
    migrate_db,
    get_system_stats,
    delete_backup_file,
    download_backup_file,
    integrity_check,
    deploy_app,
    optimize_database,
    create_missing_tables,
    create_complete_backup,
    create_school_backup,
    restore_school_backup,
    get_school_backups,
    init_annees_scolaires
)

BACKUP_DIR = 'backups'

# --- Dashboard ---
# SECURITY TODO: ce blueprint admin expose plusieurs routes sans @login_required/@role_required ici.
# Vérifier s'il existe une protection globale avant toute mise en production.
@admin_bp.route('/admin')
def dashboard():
    stats = get_system_stats()
    return render_template('dashboard.html', 
                           os=os, 
                           backup_dir=BACKUP_DIR,
                           last_backup=stats['last_backup'],
                           disk_usage=stats['disk_usage'],
                           db_version=stats['db_version'])

# --- Sauvegarde simple ---
@admin_bp.route('/admin/backup')
def backup():
    try:
        create_backup()
        flash("Sauvegarde effectuée avec succès!", "success")
    except Exception as e:
        flash(f"Erreur lors de la sauvegarde: {str(e)}", "error")
    return redirect(url_for('admin.backup_page'))

# --- Restauration simple ---
@admin_bp.route('/admin/restore/<filename>')
def restore(filename):
    try:
        restore_backup(filename)
        flash(f"Restauration depuis {filename} réussie!", "success")
    except Exception as e:
        flash(f"Erreur lors de la restauration: {str(e)}", "error")
    return redirect(url_for('admin.backup_page'))

# --- Nettoyage ---
@admin_bp.route('/admin/clean')
def clean():
    try:
        result = clean_data()
        flash(f"Nettoyage terminé: {result}", "success")
    except Exception as e:
        flash(f"Erreur lors du nettoyage: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Migration ---
@admin_bp.route('/admin/migrate')
def migrate():
    try:
        result = migrate_db()
        flash(f"Migration terminée: {result}", "success")
    except Exception as e:
        flash(f"Erreur lors de la migration: {str(e)}", "error")
    return redirect(url_for('admin.maintenance_page'))

# --- Pages ---
@admin_bp.route('/admin/backup_page')
def backup_page():
    return render_template('backup.html', os=os, backup_dir=BACKUP_DIR)

@admin_bp.route('/admin/maintenance_page')
def maintenance_page():
    return render_template('maintenance.html')

# --- Gestion des sauvegardes ---
@admin_bp.route('/admin/delete_backup/<filename>')
def delete_backup(filename):
    try:
        delete_backup_file(filename)
        flash(f"Sauvegarde {filename} supprimée avec succès!", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "error")
    return redirect(url_for('admin.backup_page'))

@admin_bp.route('/admin/download_backup/<filename>')
def download_backup(filename):
    try:
        return download_backup_file(filename)
    except Exception as e:
        flash(f"Erreur lors du téléchargement: {str(e)}", "error")
        return redirect(url_for('admin.backup_page'))

# --- Vérification d'intégrité ---
@admin_bp.route('/admin/integrity_check')
def integrity_check_route():
    """Route pour la vérification d'intégrité"""
    try:
        results, summary = integrity_check()
        flash("Vérification et correction d'intégrité terminées !", "success")
        
        return render_template(
            'maintenance.html',
            integrity_results=results,
            integrity_summary=summary
        )

    except Exception as e:
        flash(f"Erreur lors de la vérification/correction : {str(e)}", "error")
        return redirect(url_for('admin.maintenance_page'))

# --- Statistiques ---
@admin_bp.route('/admin/stats')
def stats():
    stats = get_system_stats()
    return render_template('stats.html', stats=stats)

# --- Logs ---
@admin_bp.route('/admin/logs')
def view_logs():
    """Affiche les logs système"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    logs = Log.query.order_by(Log.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('logs.html', logs=logs)

# --- Déploiement ---
@admin_bp.route('/admin/deploy', methods=['POST'])
def deploy():
    """Lance le déploiement de l'application"""
    result = deploy_app()
    
    if "Erreur" in result or "Exception" in result:
        flash(f"Erreur lors du déploiement: {result}", "error")
    else:
        flash(f"Déploiement réussi: {result}", "success")
    
    return redirect(url_for('admin.dashboard'))

# --- Optimisation ---
@admin_bp.route('/admin/optimize')
def optimize():
    """Optimise la base de données"""
    result = optimize_database()
    if result:
        flash("Base de données optimisée avec succès", "success")
    else:
        flash("Erreur lors de l'optimisation", "error")
    
    return redirect(url_for('admin.maintenance_page'))

# --- Version ---
@admin_bp.route('/admin/version')
def version_info():
    """Affiche les informations de version"""
    stats = get_system_stats()
    return render_template('version.html', stats=stats)

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

# --- Restauration par école ---
@admin_bp.route('/admin/restore_school/<filename>')
def restore_school(filename):
    """Restauration d'une sauvegarde d'école spécifique"""
    try:
        result = restore_school_backup(filename)
        flash(f"Restauration réussie: {result}", "success")
    except Exception as e:
        flash(f"Erreur lors de la restauration: {str(e)}", "error")
    return redirect(url_for('admin.backup_page'))
