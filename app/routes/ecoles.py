from . import main
from .common import (
    Ecole,
    Eleve,
    GererEcolesForm,
    Utilisateur,
    current_app,
    current_user,
    db,
    envoyer_email,
    flash,
    generate_password_hash,
    gestion_ecole,
    get_ecole_filter_query,
    jsonify,
    login_required,
    nettoyer_repertoire_ecole,
    redirect,
    render_template,

    request,
    role_required,
    session,
    url_for,
)
from app.access_codes import generate_access_code
from app.services.school_lifecycle import (
    SCHOOL_ACTIVE_STATUS,
    SCHOOL_DELETE_CONFIRMATION_PHRASE,
    days_until_school_deletion,
    is_school_deletion_eligible,
    is_school_disabled,
    school_deletion_available_at,
    utcnow,
)


@main.route('/choisir-ecole')
@login_required
def choisir_ecole():
    """Afficher automatiquement les écoles accessibles et leurs journaux/problèmes"""
    
    # Pour un admin normal : récupérer son école et ses écoles gérées
    if current_user.role != "super_admin":
        ecoles = []
        if current_user.ecole:
            ecoles.append(current_user.ecole)
        if getattr(current_user, 'ecoles_gerees', None):
            ecoles.extend(current_user.ecoles_gerees)
        # éliminer doublons
        ecoles = list({e.id: e for e in ecoles}.values())
    
    # Pour super-admin : toutes les écoles
    else:
        ecoles = get_ecole_filter_query(Ecole).all()
    
    # Préparer les données de journaux et problèmes pour chaque école
    for ecole in ecoles:
        # journaux_correction et problemes doivent être des relations SQLAlchemy
        ecole.journaux_correction = getattr(ecole, 'journaux_correction', [])
        ecole.problemes = getattr(ecole, 'problemes', [])

    return render_template('choisir_ecole.html', ecoles=ecoles)

@main.route('/admin/ecoles')
@login_required
@role_required('super_admin')
def gestion_ecoles():
    """Gestion des écoles (super-admin seulement)"""
    try:
        from app.models import Classe, Eleve, Professeur, AnneeScolaire
        ecoles = get_ecole_filter_query(Ecole).order_by(Ecole.id.desc()).all()
        total_eleves = 0
        for ecole in ecoles:
            ecole.nb_eleves = Eleve.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_classes = Classe.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_profs = Professeur.query.filter_by(ecole_id=ecole.id).count()
            ecole.deletion_available_at = school_deletion_available_at(ecole)
            ecole.deletion_days_remaining = days_until_school_deletion(ecole)
            ecole.deletion_eligible = is_school_deletion_eligible(ecole)
            total_eleves += ecole.nb_eleves
            admin_user = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()
            ecole.admin_user = admin_user
            if not ecole.email and admin_user and admin_user.email:
                ecole.email = admin_user.email
            annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole.id, statut='active').first()
            ecole.annee_active = annee_active.nom if annee_active else 'Non configurée'

        stats = {
            'total_ecoles': len(ecoles),
            'ecoles_actives': sum(1 for e in ecoles if e.statut in ('actif', 'active')),
            'ecoles_bloquees': sum(1 for e in ecoles if is_school_disabled(e)),
            'total_eleves': total_eleves,
        }

        # Récupérer (et supprimer de la session) le mot de passe auto-généré pour affichage temporaire
        mdp_auto = session.pop('_mdp_auto_ecole', None)
        mdp_auto_email = session.pop('_mdp_auto_email', None)
        mdp_auto_nom = session.pop('_mdp_auto_nom', None)

        return render_template('admin/ecoles.html', ecoles=ecoles, stats=stats,
                               mdp_auto=mdp_auto, mdp_auto_email=mdp_auto_email,
                               mdp_auto_nom=mdp_auto_nom)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur récupération écoles : {e}")
        flash("Erreur lors de la récupération des écoles.", "danger")
        return render_template('admin/ecoles.html', ecoles=[], stats={
            'total_ecoles': 0, 'ecoles_actives': 0, 'ecoles_bloquees': 0, 'total_eleves': 0
        })

@main.route('/admin/ecoles/ajouter', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def ajouter_ecole():
    """Ajouter une nouvelle ecole et son administrateur"""
    if request.method == 'POST':
        try:
            nom_ecole    = request.form.get('nom_ecole', '').strip()[:200]
            adresse      = request.form.get('adresse', '').strip()[:300]
            ville        = request.form.get('ville', '').strip()[:100]
            telephone    = request.form.get('telephone', '').strip()[:20]
            email_admin  = request.form.get('email_admin', '').strip()[:120]
            mot_de_passe_saisi = request.form.get('mot_de_passe', '').strip()
            mot_de_passe_auto = not bool(mot_de_passe_saisi)
            mot_de_passe = mot_de_passe_saisi or generate_access_code()

            if not nom_ecole:
                flash("Le nom de l'école est obligatoire.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            if not email_admin:
                flash("L'email de l'administrateur est obligatoire.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            if Utilisateur.query.filter_by(email=email_admin).first():
                flash("Cet email est déjà utilisé par un autre utilisateur.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            # Création école (un seul nom et un seul email)
            ecole = Ecole(
                nom=nom_ecole,
                adresse=adresse or None,
                ville=ville or None,
                telephone=telephone or None,
                email=email_admin,
                statut='active'
            )
            db.session.add(ecole)
            db.session.flush()

            # Création admin lié à l'école
            admin = Utilisateur(
                nom=nom_ecole,
                email=email_admin,
                role='admin',
                mot_de_passe=generate_password_hash(mot_de_passe),
                ecole_id=ecole.id,
                statut='actif'
            )
            db.session.add(admin)
            db.session.commit()

            sujet = "Bienvenue sur KLASORA — Votre espace école est prêt"
            corps = render_template(
                'emails/bienvenue_ecole.html',
                ecole=ecole,
                admin=admin,
                mot_de_passe=mot_de_passe
            )
            email_ok = envoyer_email(admin.email, sujet, corps, context="welcome_school")

            # Stocker le mot de passe auto-généré en session pour affichage temporaire
            if mot_de_passe_auto:
                session['_mdp_auto_ecole'] = mot_de_passe
                session['_mdp_auto_email'] = email_admin
                session['_mdp_auto_nom'] = nom_ecole

            if email_ok:
                current_app.logger.info("EMAIL_SUCCESS_HANDLED type=welcome_school recipient=%s", admin.email)
                flash(f"École « {ecole.nom} » créée avec succès ✅", "success")
            else:
                current_app.logger.warning("EMAIL_FAILED_HANDLED type=welcome_school recipient=%s", admin.email)
                flash(
                    f"École « {ecole.nom} » créée avec succès, mais l'email de bienvenue n'a pas pu être envoyé.",
                    "warning"
                )
            return redirect(url_for('main.gestion_ecoles'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création école : {e}")
            flash("Erreur lors de la création de l'école.", "danger")

    return render_template('admin/ajouter_ecole.html')

@main.route('/api/ecoles')
@login_required
def api_ecoles():
    """API pour récupérer les écoles accessibles"""
    try:
        if current_user.role == 'super_admin':
            ecoles = get_ecole_filter_query(Ecole).all()
        else:
            ecoles = []
            if getattr(current_user, 'ecole', None):
                ecoles.append(current_user.ecole)
            if getattr(current_user, 'ecoles_gerees', None):
                ecoles.extend(current_user.ecoles_gerees)
            # éliminer doublons
            ecoles = list({e.id: e for e in ecoles}.values())

        return jsonify([{'id': e.id, 'nom': e.nom} for e in ecoles])
    except Exception as e:
        current_app.logger.error(f"Erreur API écoles : {e}")
        return jsonify([]), 500


@main.route('/api/ecoles/<int:ecole_id>/details')
@login_required
@role_required('super_admin')
def api_ecole_details(ecole_id):
    """API renvoyant les informations complètes d'une école pour la modale 'Voir plus'"""
    try:
        from app.models import Classe, Eleve, Professeur, AnneeScolaire
        ecole = Ecole.query.get_or_404(ecole_id)
        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole.id, statut='active').first()

        data = {
            'id': ecole.id,
            'nom': ecole.nom,
            'adresse': ecole.adresse or '',
            'ville': ecole.ville or '',
            'telephone': ecole.telephone or '',
            'email': ecole.email or '',
            'slogan': ecole.slogan or ecole.devise or '',
            'statut': ecole.statut,
            'motif_blocage': ecole.motif_blocage or '',
            'disabled_at': ecole.disabled_at.strftime('%d/%m/%Y %H:%M') if ecole.disabled_at else None,
            'date_creation': ecole.date_creation.strftime('%d/%m/%Y à %H:%M') if ecole.date_creation else '',
            'logo_url': url_for('static', filename=ecole.logo_path) if ecole.logo_path and ecole.logo_path != 'default_logo.png' else '',
            'onboarding_complete': bool(ecole.onboarding_complete),
            'annee_active': annee_active.nom if annee_active else 'Non configurée',
            'nb_eleves': Eleve.query.filter_by(ecole_id=ecole.id).count(),
            'nb_classes': Classe.query.filter_by(ecole_id=ecole.id).count(),
            'nb_profs': Professeur.query.filter_by(ecole_id=ecole.id).count(),
        }
        return jsonify({'success': True, 'ecole': data})
    except Exception as e:
        current_app.logger.error(f"Erreur API détails école {ecole_id}: {e}")
        return jsonify({'success': False, 'message': "Erreur lors de la récupération des détails de l'école"}), 500


@main.route('/admin/ecoles/<int:ecole_id>/assigner', methods=['POST'])
@login_required
@role_required('super_admin')
def assigner_ecole(ecole_id):
    """Assigner une école ? un gestionnaire"""
    try:
        utilisateur_id = int(request.form.get('utilisateur_id'))
        utilisateur = Utilisateur.query.get_or_404(utilisateur_id)
        ecole = Ecole.query.get_or_404(ecole_id)

        # Supprimer association existante si nécessaire
        db.session.execute(
            gestion_ecole.delete().where(
                (gestion_ecole.c.utilisateur_id == utilisateur_id) &
                (gestion_ecole.c.ecole_id == ecole_id)
            )
        )

        # Ajouter association
        db.session.execute(
            gestion_ecole.insert().values(
                utilisateur_id=utilisateur_id,
                ecole_id=ecole_id
            )
        )
        db.session.commit()
        flash(f"École '{ecole.nom}' assignée ? {utilisateur.prenom} {utilisateur.nom}", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur assignation école : {e}")
        flash("Erreur lors de l'assignation de l'école.", "danger")
    return redirect(request.referrer or url_for('main.gestion_ecoles'))

@main.route('/admin/utilisateur/<int:user_id>/ecoles', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def gerer_ecoles_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)
    toutes_ecoles = get_ecole_filter_query(Ecole).all()
    form = GererEcolesForm()

    ecoles_actuelles = [e.id for e in getattr(utilisateur, 'ecoles_gerees', [])]

    if form.validate_on_submit():
        try:
            ecoles_selectionnees = [int(ecole_id) for ecole_id in request.form.getlist('ecoles')]
            ecoles_decocher = set(ecoles_actuelles) - set(ecoles_selectionnees)

            erreurs = []
            for ecole_id in ecoles_decocher:
                nb_eleves = Eleve.query.filter_by(ecole_id=ecole_id).count()
                if nb_eleves > 0:
                    ecole = Ecole.query.get(ecole_id)
                    erreurs.append(f"L'école '{ecole.nom}' contient encore {nb_eleves} élèves et ne peut pas être retirée.")

            if erreurs:
                for err in erreurs:
                    flash(err, 'danger')
                return redirect(url_for('main.gerer_ecoles_utilisateur', user_id=user_id))

            # Supprimer associations existantes
            db.session.execute(gestion_ecole.delete().where(gestion_ecole.c.utilisateur_id == user_id))

            # Ajouter nouvelles associations
            for ecole_id in ecoles_selectionnees:
                db.session.execute(gestion_ecole.insert().values(
                    utilisateur_id=user_id,
                    ecole_id=ecole_id
                ))

            db.session.commit()
            flash("Écoles assignées avec succès", "success")
            return redirect(url_for('main.gestion_utilisateurs'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur gestion écoles utilisateur : {e}")
            flash(f"Erreur : {str(e)}", "danger")

    # Statistiques globales
    nb_ecoles = Ecole.query.count()
    nb_eleves = Eleve.query.count()
    nb_parents = Utilisateur.query.filter_by(role='parent').count()
    nb_enseignants = Utilisateur.query.filter_by(role='professeur').count()
    nb_admins = Utilisateur.query.filter_by(role='admin').count()

    stats_ecoles = []
    for ecole in toutes_ecoles:
        nb_eleve = Eleve.query.filter_by(ecole_id=ecole.id).count()
        stats_ecoles.append({
            'id': ecole.id,
            'nom': ecole.nom,
            'eleves': nb_eleve
        })

    return render_template(
        "admin/gerer_ecoles_utilisateur.html",
        utilisateur=utilisateur,
        toutes_ecoles=toutes_ecoles,
        ecoles_actuelles=ecoles_actuelles,
        form=form,
        nb_ecoles=nb_ecoles,
        nb_eleves=nb_eleves,
        nb_parents=nb_parents,
        nb_enseignants=nb_enseignants,
        nb_admins=nb_admins,
        stats_ecoles=stats_ecoles
    )

def safe_delete_ecole(ecole_id):
    """Supprime proprement et en cascade toutes les données liées à une école"""
    from app.models import (
        Absence, Alerte, AnneeScolaire, ArchiveAbsence, ArchiveNote,
        Bulletin, Classe, Cours, Eleve, EmploiTemps, HistoriqueImport,
        Inscription, JournalCorrection, Log, Note, Paiement,
        PeriodeBulletin, Presence, Professeur, Utilisateur
    )
    ecole = Ecole.query.get_or_404(ecole_id)
    nom_ecole = ecole.nom

    # 1. IDs des élèves et utilisateurs non super-admin
    eleves = Eleve.query.filter_by(ecole_id=ecole_id).all()
    eleve_ids = [el.id for el in eleves]

    users = Utilisateur.query.filter_by(ecole_id=ecole_id).all()
    user_ids = [u.id for u in users if u.role != 'super_admin']

    # 2. Données liées aux élèves
    if eleve_ids:
        Presence.query.filter(Presence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Bulletin.query.filter(Bulletin.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Inscription.query.filter(Inscription.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        ArchiveNote.query.filter(ArchiveNote.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        ArchiveAbsence.query.filter(ArchiveAbsence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Note.query.filter(Note.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Absence.query.filter(Absence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Paiement.query.filter(Paiement.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)

    # 3. Données scolaires directes
    Note.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Absence.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Paiement.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    EmploiTemps.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    PeriodeBulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    JournalCorrection.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Log.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

    # 4. Tables de liaison n-m
    for table_query in [
        "DELETE FROM professeur_classes WHERE ecole_id = :eid",
        "DELETE FROM ecole_cours WHERE ecole_id = :eid",
        "DELETE FROM gestion_ecole WHERE ecole_id = :eid"
    ]:
        try:
            db.session.execute(db.text(table_query), {'eid': ecole_id})
        except Exception as e:
            current_app.logger.debug(f"Nettoyage association ({table_query}): {e}")

    # 5. Cours, Élèves, Classes, Années, Professeurs
    Cours.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Eleve.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Classe.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    AnneeScolaire.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Professeur.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

    # 6. Données liées aux utilisateurs de l'école
    if user_ids:
        HistoriqueImport.query.filter(HistoriqueImport.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)
        Alerte.query.filter(Alerte.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)
        Log.query.filter(Log.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)

    # 7. Utilisateurs non-superadmin
    Utilisateur.query.filter(
        Utilisateur.ecole_id == ecole_id,
        Utilisateur.role != 'super_admin'
    ).delete(synchronize_session=False)

    # 8. Nettoyer la session courante si nécessaire
    from flask import has_request_context
    if has_request_context() and session.get('ecole_id') == ecole_id:
        autre_ecole = Ecole.query.filter(Ecole.id != ecole_id).first()
        session['ecole_id'] = autre_ecole.id if autre_ecole else None

    # 9. Supprimer l'école elle-même
    db.session.delete(ecole)
    db.session.commit()

    # 10. Nettoyage physique du répertoire des fichiers de l'école
    try:
        from app.utils import nettoyer_repertoire_ecole
        nettoyer_repertoire_ecole(ecole_id)
    except Exception as e:
        current_app.logger.error(
            f"Erreur lors du nettoyage du répertoire de l'école {ecole_id}: {e}"
        )

    return nom_ecole



@main.route('/admin/ecoles/<int:ecole_id>/modifier', methods=['POST'])
@login_required
@role_required('super_admin')
def modifier_ecole(ecole_id):
    """Modifier les informations d'un établissement (un seul nom, un seul email)"""
    ecole = Ecole.query.get_or_404(ecole_id)
    try:
        nom = request.form.get('nom', '').strip()[:200]
        email = request.form.get('email', '').strip()[:120]
        telephone = request.form.get('telephone', '').strip()[:20]
        adresse = request.form.get('adresse', '').strip()[:300]
        ville = request.form.get('ville', '').strip()[:100]

        if not nom:
            flash("Le nom de l'école est obligatoire.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        if not email:
            flash("L'email de l'école est obligatoire.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        admin = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()

        # Vérifier unicité email si modifié
        conflit = Utilisateur.query.filter(
            Utilisateur.email == email,
            Utilisateur.id != (admin.id if admin else None)
        ).first()
        if conflit:
            flash("Cet email est déjà utilisé par un autre compte utilisateur.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        # Mise à jour école
        ecole.nom = nom
        ecole.email = email
        ecole.telephone = telephone or None
        ecole.adresse = adresse or None
        ecole.ville = ville or None

        # Synchronisation de l'administrateur de l'école
        if admin:
            admin.email = email
            admin.nom = nom

        db.session.commit()
        flash(f"L'école « {ecole.nom} » a été modifiée avec succès ✅", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur modification école {ecole_id}: {e}")
        flash("Une erreur est survenue lors de la modification de l'école.", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/admin/ecoles/<int:ecole_id>/bloquer', methods=['POST'])
@login_required
@role_required('super_admin')
def bloquer_ecole(ecole_id):
    """Bloquer ou débloquer un établissement avec motif explicite"""
    ecole = Ecole.query.get_or_404(ecole_id)
    action = request.form.get('action', '').strip()  # 'bloquer' ou 'debloquer'
    motif = request.form.get('motif_blocage', '').strip()

    try:
        if action == 'bloquer':
            ecole.statut = 'bloque'
            ecole.motif_blocage = motif or 'Suspension administrative'
            ecole.disabled_at = utcnow()
            db.session.commit()
            flash(f"L'école « {ecole.nom} » a été bloquée 🛑 (Motif: {ecole.motif_blocage})", "warning")
        elif action == 'debloquer':
            ecole.statut = SCHOOL_ACTIVE_STATUS
            ecole.motif_blocage = None
            ecole.disabled_at = None
            db.session.commit()
            flash(f"L'école « {ecole.nom} » a été débloquée et réactivée avec succès 🟢", "success")
        else:
            flash("Action non reconnue.", "danger")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur changement statut école {ecole_id}: {e}")
        flash("Une erreur est survenue lors de l'opération sur l'école.", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/admin/ecoles/<int:ecole_id>/supprimer', methods=['POST'])
@login_required
@role_required('super_admin')
def supprimer_ecole_action(ecole_id):
    """Supprimer définitivement une école après délai et confirmation forte."""
    ecole = Ecole.query.get_or_404(ecole_id)
    confirmation_nom = (request.form.get('confirmation_nom') or '').strip()
    confirmation_phrase = (request.form.get('confirmation_phrase') or '').strip()

    if not is_school_disabled(ecole):
        flash("Suppression définitive refusée : l'école doit d'abord être désactivée.", "danger")
        return redirect(url_for('main.gestion_ecoles'))

    if not ecole.disabled_at:
        flash("Suppression définitive refusée : aucune date de désactivation valide n'est enregistrée.", "danger")
        return redirect(url_for('main.gestion_ecoles'))

    if not is_school_deletion_eligible(ecole):
        jours = days_until_school_deletion(ecole)
        flash(f"Suppression définitive refusée : disponible dans {jours} jour(s).", "danger")
        return redirect(url_for('main.gestion_ecoles'))

    if confirmation_nom != ecole.nom:
        flash("Suppression définitive refusée : le nom de l'école ne correspond pas exactement.", "danger")
        return redirect(url_for('main.gestion_ecoles'))

    if confirmation_phrase != SCHOOL_DELETE_CONFIRMATION_PHRASE:
        flash("Suppression définitive refusée : la phrase de confirmation est incorrecte.", "danger")
        return redirect(url_for('main.gestion_ecoles'))

    try:
        current_app.logger.warning(
            "SCHOOL_PERMANENT_DELETE_CONFIRMED ecole_id=%s ecole_nom=%s disabled_at=%s deleted_at=%s super_admin_id=%s super_admin_email=%s",
            ecole.id,
            ecole.nom,
            ecole.disabled_at.isoformat() if ecole.disabled_at else None,
            utcnow().isoformat(),
            current_user.id,
            current_user.email,
        )
        nom_ecole = safe_delete_ecole(ecole_id)
        flash(f"L'école « {nom_ecole} » et toutes ses données associées ont été supprimées définitivement 🗑️", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression école {ecole_id}: {e}")
        flash(f"Erreur lors de la suppression de l'école : {str(e)}", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/admin/ecoles/<int:ecole_id>/reset-mdp', methods=['POST'])
@login_required
@role_required('super_admin')
def reset_mdp_ecole(ecole_id):
    """Génère un nouveau mot de passe pour l'administrateur de l'école et l'envoie par email"""
    try:
        ecole = Ecole.query.get_or_404(ecole_id)
        admin = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()

        if not admin:
            return jsonify({'success': False, 'message': "Aucun compte administrateur trouvé pour cette école."}), 404

        if not admin.email:
            return jsonify({'success': False, 'message': "L'administrateur n'a pas d'adresse email valide."}), 400

        nouveau_mdp = generate_access_code()
        admin.mot_de_passe = generate_password_hash(nouveau_mdp)
        db.session.commit()

        sujet = f"Réinitialisation de votre mot de passe — KLASORA ({ecole.nom})"
        corps = render_template(
            'emails/reinitialisation_mdp.html',
            ecole=ecole,
            admin=admin,
            mot_de_passe=nouveau_mdp
        )
        email_ok = envoyer_email(admin.email, sujet, corps, context="reset_school_mdp")

        return jsonify({
            'success': True,
            'nouveau_mdp': nouveau_mdp,
            'email_envoye': email_ok,
            'email': admin.email,
            'ecole_nom': ecole.nom
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur réinitialisation MDP école {ecole_id}: {e}")
        return jsonify({'success': False, 'message': "Une erreur interne est survenue."}), 500


@main.route('/api/ecoles/<int:ecole_id>', methods=['DELETE'])
@login_required
@role_required('super_admin')
def supprimer_ecole(ecole_id):
    """API DELETE désactivée pour empêcher le contournement du délai."""
    return jsonify({
        'success': False,
        'message': "Suppression directe désactivée. Utilisez la confirmation forte après 30 jours de désactivation."
    }), 405


@main.route('/api/ecoles/<int:ecole_id>/status', methods=['PUT'])
@login_required
@role_required('super_admin')
def toggle_ecole_status(ecole_id):
    """Changer le statut d'une école (API bascule active/inactive)"""
    if current_user.role != 'super_admin':
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    ecole = Ecole.query.get_or_404(ecole_id)
    if ecole.statut in ('active', 'actif'):
        ecole.statut = 'inactive'
        ecole.disabled_at = utcnow()
        ecole.motif_blocage = ecole.motif_blocage or 'Désactivation administrative'
    else:
        ecole.statut = SCHOOL_ACTIVE_STATUS
        ecole.disabled_at = None
        ecole.motif_blocage = None
    db.session.commit()

    return jsonify({'success': True, 'new_status': ecole.statut})


@main.route('/profil-ecole', methods=['GET', 'POST'])
@login_required
def profil_ecole():
    """Gestion du profil et de l'identité visuelle de l'établissement par son administrateur"""
    ecole = getattr(current_user, 'ecole', None)
    if not ecole and getattr(current_user, 'ecole_id', None):
        ecole = Ecole.query.get(current_user.ecole_id)
    if not ecole:
        flash("Aucun établissement associé à votre compte.", "warning")
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        try:
            nom = request.form.get('nom', '').strip()[:200]
            adresse = request.form.get('adresse', '').strip()[:300]
            telephone = request.form.get('telephone', '').strip()[:20]
            email = request.form.get('email', '').strip()[:120]
            directeur = request.form.get('directeur', '').strip()[:100]
            ville = request.form.get('ville', '').strip()[:100]
            slogan = (request.form.get('slogan') or request.form.get('devise', '')).strip()[:250]

            if nom:
                ecole.nom = nom
                admin = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()
                if admin:
                    admin.nom = nom
            ecole.adresse = adresse or None
            ecole.telephone = telephone or None
            if current_user.role == 'super_admin':
                ecole.email = email or None
            ecole.directeur = directeur or None
            if hasattr(ecole, 'ville'):
                ecole.ville = ville or None
            if slogan:
                ecole.slogan = slogan

            if 'logo' in request.files:
                file = request.files['logo']
                if file and file.filename:
                    from app.utils import validate_and_save_school_logo
                    ok, err = validate_and_save_school_logo(
                        file, ecole, current_app.static_folder
                    )
                    if not ok:
                        flash(err, "danger")
                        return redirect(url_for('main.profil_ecole'))

            db.session.commit()
            flash("Identité et profil de l'établissement mis à jour avec succès ✅", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur mise à jour profil école : {e}")
            flash("Erreur lors de la mise à jour du profil de l'établissement.", "danger")

        return redirect(url_for('main.profil_ecole'))

    return render_template('profil_ecole.html', ecole=ecole)

