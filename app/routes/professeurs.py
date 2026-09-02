from . import main
from .common import (
    AnneeScolaire,
    AssignerClassesForm,
    Classe,
    Cours,
    DeleteForm,
    Professeur,
    ProfesseurForm,
    Utilisateur,
    current_app,
    current_user,
    datetime,
    db,
    flash,
    generate_password_hash,
    joinedload,
    json,
    login_required,
    professeur_classes,
    redirect,
    render_template,
    request,
    role_required,
    url_for,
)
from app.services import check_ecole_access


@main.route('/professeurs')
@login_required
@role_required('admin')
def professeurs():
    """Liste de tous les professeurs avec pagination filtrée par école et recherche optionnelle"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = request.args.get('search', '', type=str).strip()

    # Filtrage par école de l'utilisateur
    profs_query = Professeur.query.filter_by(ecole_id=current_user.ecole_id)

    # Recherche sur nom, prénom, email et spécialité
    if search:
        profs_query = profs_query.filter(
            db.or_(
                Professeur.nom.ilike(f"%{search}%"),
                Professeur.prenom.ilike(f"%{search}%"),
                Professeur.email.ilike(f"%{search}%"),
                Professeur.specialite.ilike(f"%{search}%")
            )
        )

    # Tri et pagination
    profs_query = profs_query.order_by(Professeur.nom, Professeur.prenom)
    profs_pagination = profs_query.paginate(page=page, per_page=per_page, error_out=False)
    delete_form = DeleteForm()

    return render_template(
        'professeurs.html',
        professeurs=profs_pagination,
        delete_form=delete_form,
        search=search
    )

@main.route('/ajouter_professeur', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_professeur():
    """Ajout d'un professeur avec contrôle de cohérence et notifications"""
    form = ProfesseurForm()
    ecole_id = current_user.ecole_id

    if form.validate_on_submit():
        try:
            # ---------------- Code professeur ----------------
            code_prof = form.code_prof.data.strip() if form.code_prof.data else Professeur.generer_code()

            # ---------------- Vérification unicité ----------------
            if Professeur.query.filter_by(code_prof=code_prof, ecole_id=ecole_id).first():
                flash("Ce code professeur existe déjà dans votre école.", "danger")
                return redirect(url_for('main.ajouter_professeur'))

            if Utilisateur.query.filter_by(email=form.email.data, ecole_id=ecole_id).first():
                flash("Cet email est déjà utilisé dans votre école.", "danger")
                return redirect(url_for('main.ajouter_professeur'))

            # ---------------- Création utilisateur ----------------
            utilisateur = Utilisateur(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                email=form.email.data.lower(),
                mot_de_passe=generate_password_hash(code_prof),
                role="professeur",
                telephone=form.telephone.data.strip() if form.telephone.data else None,
                statut="actif",
                ecole_id=ecole_id
            )

            # ---------------- Création professeur ----------------
            nouveau_professeur = Professeur(
                nom=form.nom.data.strip(),
                prenom=form.prenom.data.strip(),
                date_naissance=form.date_naissance.data,
                adresse=form.adresse.data.strip() if form.adresse.data else None,
                telephone=form.telephone.data.strip() if form.telephone.data else None,
                email=form.email.data.lower(),
                specialite=form.specialite.data,
                matieres_enseignees=form.matieres_enseignees.data,
                code_prof=code_prof,
                ecole_id=ecole_id,
                utilisateur=utilisateur
            )

            # ---------------- Commit unique ----------------
            db.session.add(utilisateur)
            db.session.add(nouveau_professeur)
            db.session.commit()

            # ---------------- Journalisation ----------------
            current_app.log_correction(
                action="ajout",
                description=f"Professeur ajouté : {nouveau_professeur.nom} {nouveau_professeur.prenom}",
                ecole_id=ecole_id,
                cible_type="professeur",
                cible_id=nouveau_professeur.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "nom": nouveau_professeur.nom,
                    "prenom": nouveau_professeur.prenom,
                    "email": nouveau_professeur.email,
                    "code_prof": nouveau_professeur.code_prof
                }, ensure_ascii=False),
                niveau="info"
            )

            # ---------------- Envoi email ----------------
            if nouveau_professeur.email:
                try:
                    from app.notifications import envoyer_email
                    sujet = "Création de votre compte professeur"
                    message = f"""<html>
                    <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                        <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                            <h2 style="color:#2196F3;">Bonjour {nouveau_professeur.prenom or ''} {nouveau_professeur.nom},</h2>
                            <p>Votre compte professeur a été créé avec succès !</p>
                            <h3>Vos identifiants :</h3>
                            <ul>
                                <li><b>Email:</b> {nouveau_professeur.email}</li>
                                <li><b>Mot de passe:</b> {code_prof}</li>
                            </ul>
                            <p><a href="{request.host_url}login" style="display:inline-block; padding:10px 20px; background:#2196F3; color:#fff; text-decoration:none; border-radius:5px;">Se connecter</a></p>
                            <hr style="border:none; border-top:1px solid #eee;">
                            <p style="font-size:12px; color:#555;">Ne partagez pas vos identifiants.<br>Cordialement,<br>L'administration</p>
                        </div>
                    </body>
                    </html>"""
                    envoyer_email(nouveau_professeur.email, sujet, message)
                    current_app.logger.info(f"Email envoyé à {nouveau_professeur.email}")
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email: {e}")
                    flash("Professeur ajouté mais email non envoyé.", "warning")

            # ---------------- Notification Telegram ----------------
            try:
                from app.notifications import envoyer_telegram
                telegram_message = (
                    f"👨‍🏫 Nouveau compte professeur créé !\n"
                    f"Nom: {nouveau_professeur.prenom or ''} {nouveau_professeur.nom}\n"
                    f"Email: {nouveau_professeur.email}\n"
                    f"Mot de passe: {code_prof}\n"
                    f"Connexion: {request.host_url}login"
                )
                envoyer_telegram(telegram_message)
                current_app.logger.info("Notification Telegram envoyée")
            except Exception as e:
                current_app.logger.error(f"Erreur envoi Telegram: {e}")

            flash(f"✅ Professeur ajouté avec succès. Code d'accès: {code_prof}", "success")
            return redirect(url_for('main.professeurs'))

        except Exception as e:
            db.session.rollback()
            import traceback
            current_app.logger.error(f"Erreur ajout professeur: {e}\n{traceback.format_exc()}")
            flash("❌ Erreur lors de l'ajout du professeur.", "danger")

    return render_template('ajouter_professeur.html', form=form)

@main.route('/professeur/<int:id>')
@login_required
@role_required('admin')
def professeur_details(id):
    professeur = Professeur.query.options(
        joinedload(Professeur.cours).joinedload(Cours.notes)
    ).get_or_404(id)

    if not check_ecole_access(professeur, "professeur"):
        return redirect(url_for('main.profile'))

    total_eleves = len(set(n.eleve_id for c in professeur.cours for n in c.notes))
    total_notes = sum(len(c.notes) for c in professeur.cours)

    return render_template('professeur_details.html',
                           professeur=professeur,
                           cours=professeur.cours,
                           total_eleves=total_eleves,
                           total_notes=total_notes)

@main.route('/professeur/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_professeur(id):
    professeur = Professeur.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    form = ProfesseurForm(obj=professeur)

    if form.validate_on_submit():
        email = form.email.data.lower() if form.email.data else None
        if email:
            doublon_prof = Professeur.query.filter(
                Professeur.email == email,
                Professeur.id != professeur.id,
                Professeur.ecole_id == current_user.ecole_id
            ).first()
            doublon_user = Utilisateur.query.filter(
                Utilisateur.email == email,
                Utilisateur.id != professeur.utilisateur_id
            ).first()
            if doublon_prof or doublon_user:
                flash("Cet email est déjà utilisé.", "danger")
                return redirect(url_for('main.modifier_professeur', id=professeur.id))

        professeur.nom = form.nom.data.strip()
        professeur.prenom = form.prenom.data.strip()
        professeur.date_naissance = form.date_naissance.data
        professeur.adresse = form.adresse.data.strip() if form.adresse.data else None
        professeur.telephone = form.telephone.data.strip() if form.telephone.data else None
        professeur.email = email
        professeur.specialite = form.specialite.data
        professeur.matieres_enseignees = form.matieres_enseignees.data
        if form.code_prof.data:
            professeur.code_prof = form.code_prof.data.strip()

        if professeur.utilisateur:
            professeur.utilisateur.nom = professeur.nom
            professeur.utilisateur.prenom = professeur.prenom
            professeur.utilisateur.email = professeur.email
            professeur.utilisateur.telephone = professeur.telephone

        db.session.commit()
        flash("Professeur modifié avec succès.", "success")
        return redirect(url_for('main.professeur_details', id=professeur.id))

    return render_template('modifier_professeur.html', form=form, professeur=professeur)

@main.route('/professeur/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_professeur(id):
    professeur = Professeur.query.get_or_404(id)

    # 🛡️ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and professeur.ecole_id != current_user.ecole_id:
        flash("Action non autorisée : ce professeur appartient à une autre école.", "danger")
        return redirect(url_for('main.professeurs'))

    # Vérifier s'il y a des cours associés
    if professeur.cours:
        flash("Impossible de supprimer ce professeur car il a des cours associés.", "danger")
        return redirect(url_for('main.professeurs'))

    try:
        # Supprimer aussi l'utilisateur associé si existe
        if professeur.utilisateur_id:
            utilisateur = Utilisateur.query.get(professeur.utilisateur_id)
            if utilisateur:
                db.session.delete(utilisateur)

        db.session.delete(professeur)
        db.session.commit()
        current_app.logger.info(f"Professeur supprimé : {professeur.nom} (ID={professeur.id}) par {current_user.email}")
        flash("Professeur supprimé avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur lors de la suppression du professeur {professeur.id} : {e}")
        flash("Erreur lors de la suppression du professeur.", "danger")

    return redirect(url_for('main.professeurs'))

@login_required
@role_required('admin')
def supprimer_professeur_route(id):
    """Supprimer un professeur"""
    professeur = Professeur.query.get_or_404(id)
    
    # Vérifier s'il y a des cours associés
    if professeur.cours:
        flash("Impossible de supprimer ce professeur car il a des cours associés.", "danger")
        return redirect(url_for('main.profile'))
    
    # Supprimer aussi l'utilisateur associé si existe
    if professeur.utilisateur_id:
        utilisateur = Utilisateur.query.get(professeur.utilisateur_id)
        if utilisateur:
            db.session.delete(utilisateur)
    
    db.session.delete(professeur)
    db.session.commit()
    flash("Professeur supprimé avec succès.", "success")
    return redirect(url_for('main.profile'))

@main.route('/professeur/<int:id>/assigner_classes', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def assigner_classes_professeur(id):
    professeur = Professeur.query.get_or_404(id)

    # ✅ Vérification multi-école
    if professeur.ecole_id != current_user.ecole_id:
        flash("Accès refusé : ce professeur appartient à une autre école", "danger")
        return redirect(url_for("main.professeurs"))

    form = AssignerClassesForm()

    # Classes de l'école et année scolaire active
    classes_ecole = Classe.query.join(AnneeScolaire).filter(
        AnneeScolaire.ecole_id == current_user.ecole_id,
        AnneeScolaire.statut == "active"
    ).all()

    form.classes.choices = [(c.id, f"{c.nom} - {c.niveau}") for c in classes_ecole]

    if form.validate_on_submit():
        try:
            # Supprimer anciennes assignations
            db.session.execute(
                professeur_classes.delete().where(
                    professeur_classes.c.professeur_id == professeur.id
                )
            )

            # Ajouter nouvelles classes
            for classe_id in form.classes.data:
                classe = Classe.query.get(classe_id)
                if classe and classe.ecole_id == current_user.ecole_id:  # ✅ Sécurité en plus
                    db.session.execute(
                        professeur_classes.insert().values(
                            professeur_id=professeur.id,
                            classe_id=classe_id,
                            ecole_id=classe.ecole_id,
                            date_assignation=datetime.utcnow()
                        )
                    )

            db.session.commit()
            db.session.refresh(professeur)

            # Journalisation
            current_app.log_correction(
                action="modification",
                description=f"Assignation classes pour {professeur.prenom} {professeur.nom}",
                ecole_id=professeur.ecole_id,
                cible_type="professeur",
                cible_id=professeur.id,
                ancienne_valeur=None,
                nouvelle_valeur=f"Classes: {[c.nom for c in professeur.classes_assignees]}",
                niveau="info"
            )

            flash(f"Classes assignées avec succès à {professeur.prenom} {professeur.nom}", "success")
            return redirect(url_for('main.professeur_details', id=professeur.id))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'assignation des classes", "danger")
            current_app.logger.error(f"Erreur assignation classes: {e}")

    form.classes.data = [c.id for c in professeur.classes_assignees.all()]

    return render_template(
        'assigner_classes.html',
        form=form,
        professeur=professeur,
        classes_ecole=classes_ecole
    )

@main.route("/mes_classes")
@login_required
@role_required('enseignant', 'professeur')  # seulement pour consultation
def mes_classes():
    # Récupération de l'objet Professeur lié à l'utilisateur
    prof = current_user.professeur_rel
    if not prof:
        flash("Aucune information de professeur trouvée.", "warning")
        return redirect(url_for('main.index'))

    # Récupérer uniquement les classes assignées au professeur
    try:
        classes = prof.classes_assignees.all()  # si lazy='dynamic'
    except AttributeError:
        classes = prof.classes_assignees  # si lazy='select'

    return render_template("mes_classes.html", classes=classes)
