from . import main
from .common import (
    AnneeScolaire,
    AssignerClassesForm,
    Classe,
    Cours,
    DeleteForm,
    Absence,
    Inscription,
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
    jsonify,
    login_required,
    professeur_classes,
    redirect,
    render_template,
    request,
    role_required,
    url_for,
)
from app.services import check_ecole_access
from app.services.annees_scolaires import get_annee_consultee
from app.services.classes_annuelles import classe_est_ouverte


def _matiere_affectation_professeur(professeur):
    valeurs_vides = {"", "non renseignee", "non renseignée", "non defini", "non défini"}
    candidats = [
        getattr(professeur, "matiere", None),
        professeur.specialite,
        professeur.matieres_enseignees.split(",")[0].strip() if professeur.matieres_enseignees else None,
    ]
    for candidat in candidats:
        valeur = (candidat or "").strip()
        if valeur and valeur.lower() not in valeurs_vides:
            return valeur
    return ""


@main.route('/professeurs')
@login_required
@role_required('admin')
def professeurs():
    """Liste de tous les professeurs avec pagination filtrée par école et recherche multi-critères"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = (request.args.get('search') or request.args.get('q') or '').strip()
    classe_id = request.args.get('classe_id', type=int) or request.args.get('classe', type=int)
    matiere = (request.args.get('matiere') or '').strip()
    statut = (request.args.get('statut') or '').strip().lower()

    annee = get_annee_consultee(current_user.ecole_id)
    classes = Classe.query.filter_by(ecole_id=current_user.ecole_id, annee_scolaire_id=annee.id).order_by(Classe.nom).all() if annee else []

    # Filtrage par école de l'utilisateur
    profs_query = Professeur.query.filter_by(ecole_id=current_user.ecole_id)

    # Recherche multi-champs
    if search:
        pattern = f"%{search}%"
        profs_query = profs_query.filter(
            db.or_(
                Professeur.nom.ilike(pattern),
                Professeur.prenom.ilike(pattern),
                Professeur.email.ilike(pattern),
                Professeur.telephone.ilike(pattern),
                Professeur.code_prof.ilike(pattern),
                Professeur.specialite.ilike(pattern),
                Professeur.matieres_enseignees.ilike(pattern)
            )
        )

    # Filtre par classe
    if classe_id:
        classe_valide = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
        if not classe_valide:
            profs_query = profs_query.filter(Professeur.id == -1)
        else:
            profs_query = profs_query.filter(
                db.or_(
                    Professeur.classes_assignees.any(Classe.id == classe_id),
                    Professeur.cours.any(Cours.classe_id == classe_id)
                )
            )

    # Filtre par matière
    if matiere:
        m_pattern = f"%{matiere}%"
        profs_query = profs_query.filter(
            db.or_(
                Professeur.specialite.ilike(m_pattern),
                Professeur.matieres_enseignees.ilike(m_pattern),
                Professeur.cours.any(Cours.matiere.ilike(m_pattern))
            )
        )

    # Filtre par statut utilisateur
    if statut:
        profs_query = profs_query.join(Professeur.utilisateur).filter(Utilisateur.statut == statut)

    # Tri et pagination
    profs_query = profs_query.order_by(Professeur.nom, Professeur.prenom)
    profs_pagination = profs_query.paginate(page=page, per_page=per_page, error_out=False)
    delete_form = DeleteForm()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': profs_pagination.total,
            'pages': profs_pagination.pages,
            'page': profs_pagination.page,
            'professeurs': [
                {
                    'id': p.id,
                    'nom': p.nom,
                    'prenom': p.prenom,
                    'email': p.email or '',
                    'telephone': p.telephone or '',
                    'code_prof': p.code_prof or '',
                    'specialite': p.specialite or '',
                    'matieres_enseignees': p.matieres_enseignees or '',
                    'classes': [c.nom for c in p.classes_assignees]
                }
                for p in profs_pagination.items
            ]
        })

    return render_template(
        'professeurs.html',
        professeurs=profs_pagination,
        delete_form=delete_form,
        search=search,
        classe_id=classe_id,
        matiere=matiere,
        statut=statut,
        classes=classes,
        annee_consultee=annee
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
                flash("Ce code professeur existe d?j? dans votre école.", "danger")
                return redirect(url_for('main.ajouter_professeur'))

            if Utilisateur.query.filter_by(email=form.email.data, ecole_id=ecole_id).first():
                flash("Cet email est d?j? utilisé dans votre école.", "danger")
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
            email_ok = True
            if nouveau_professeur.email:
                from app.notifications import envoyer_email
                sujet = "Bienvenue sur KLASORA — Votre espace professeur est prêt"
                message = render_template(
                    'emails/bienvenue_professeur.html',
                    professeur=nouveau_professeur,
                    ecole=current_user.ecole,
                    mot_de_passe=code_prof
                )
                email_ok = envoyer_email(nouveau_professeur.email, sujet, message, context="welcome_professor")
                if email_ok:
                    current_app.logger.info("EMAIL_SUCCESS_HANDLED type=welcome_professor recipient=%s", nouveau_professeur.email)
                else:
                    current_app.logger.warning("EMAIL_FAILED_HANDLED type=welcome_professor recipient=%s", nouveau_professeur.email)
                    flash("Professeur ajouté, mais l'email de bienvenue n'a pas pu être envoyé.", "warning")

            flash(f"âœ… Professeur ajouté avec succès. Code d'accès: {code_prof}", "success")
            return redirect(url_for('main.professeurs'))

        except Exception as e:
            db.session.rollback()
            import traceback
            current_app.logger.error(f"Erreur ajout professeur: {e}\n{traceback.format_exc()}")
            flash("âŒ Erreur lors de l'ajout du professeur.", "danger")

    return render_template('ajouter_professeur.html', form=form)

@main.route('/professeur/<int:id>')
@login_required
@role_required('admin')
def professeur_details(id):
    professeur = Professeur.query.options(
        joinedload(Professeur.cours).joinedload(Cours.notes)
    ).filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

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
                flash("Cet email est d?j? utilisé.", "danger")
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
    professeur = Professeur.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    # ðŸ›¡ï¸ Sécurité multi-écoles : empêche la suppression inter-écoles
    if current_user.role != 'super_admin' and professeur.ecole_id != current_user.ecole_id:
        flash("Action non autorisée : ce professeur appartient ? une autre école.", "danger")
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
        message = "Erreur lors de la suppression du professeur."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'message': message}), 500
        flash(message, "danger")

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
    professeur = Professeur.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    if professeur.ecole_id != current_user.ecole_id:
        flash("Acces refuse : ce professeur appartient a une autre ecole", "danger")
        return redirect(url_for("main.professeurs"))

    annee_consultee = get_annee_consultee(current_user.ecole_id)
    if not annee_consultee:
        flash("Aucune annee scolaire configuree pour votre etablissement.", "warning")
        return redirect(url_for("main.professeurs"))

    est_archivee = annee_consultee.statut == "archivee"
    professeurs_ecole = (
        Professeur.query
        .filter_by(ecole_id=current_user.ecole_id)
        .order_by(Professeur.nom.asc(), Professeur.prenom.asc())
        .all()
    )
    matiere_professeur = _matiere_affectation_professeur(professeur)
    classes_annee = (
        Classe.query
        .filter(
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
        )
        .order_by(Classe.nom.asc())
        .all()
    )
    cours_annee = (
        Cours.query
        .join(Classe, Classe.id == Cours.classe_id)
        .options(joinedload(Cours.classe), joinedload(Cours.professeur))
        .filter(
            Cours.ecole_id == current_user.ecole_id,
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
        )
        .order_by(Classe.nom.asc(), Cours.nom.asc())
        .all()
    )

    if request.method == "POST":
        if est_archivee:
            flash("L'annee scolaire consultee est archivee : modification des affectations impossible.", "warning")
            return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))

        try:
            action = (request.form.get("action") or "assign").strip()
            cours_id_raw = (request.form.get("cours_id") or "").strip()
            cours = None
            if action == "assign" and cours_id_raw.startswith("new:"):
                try:
                    classe_id = int(cours_id_raw.split(":", 1)[1])
                except (TypeError, ValueError):
                    classe_id = None
                classe = (
                    Classe.query
                    .filter(
                        Classe.id == classe_id,
                        Classe.ecole_id == current_user.ecole_id,
                        Classe.annee_scolaire_id == annee_consultee.id,
                    )
                    .with_for_update()
                    .first()
                )
                if not classe:
                    flash("Classe invalide pour cette annee scolaire.", "danger")
                    return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
                if not classe_est_ouverte(classe):
                    flash("Impossible de modifier une affectation dans une classe fermee.", "warning")
                    return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
                if not matiere_professeur:
                    flash("Aucune matiere principale definie pour ce professeur.", "warning")
                    return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
                matiere_normalisee = matiere_professeur.strip().lower()
                cours = (
                    Cours.query
                    .filter(
                        Cours.ecole_id == current_user.ecole_id,
                        Cours.classe_id == classe.id,
                        db.func.lower(db.func.trim(Cours.nom)) == matiere_normalisee,
                    )
                    .first()
                )
                if not cours:
                    cours = Cours(
                        nom=matiere_professeur.strip(),
                        coefficient=1.0,
                        ecole_id=current_user.ecole_id,
                        classe_id=classe.id,
                    )
                    db.session.add(cours)
                    db.session.flush()
                    cours_annee.append(cours)
            else:
                cours_id = int(cours_id_raw) if cours_id_raw.isdigit() else None
                cours = next((item for item in cours_annee if item.id == cours_id), None)
            if not cours:
                flash("Cours invalide pour cette annee scolaire.", "danger")
                return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
            if not cours.classe or not classe_est_ouverte(cours.classe):
                flash("Impossible de modifier une affectation dans une classe fermee.", "warning")
                return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))

            ancienne_valeur = cours.professeur_id
            if action == "remove":
                if cours.professeur_id != professeur.id:
                    flash("Ce cours n'est pas affecte a ce professeur.", "warning")
                    return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
                cours.professeur_id = None
                message = "Affectation retiree. Les notes, absences et bulletins existants sont conserves."
            elif action == "change":
                nouveau_prof_id = request.form.get("nouveau_professeur_id", type=int)
                nouveau_prof = Professeur.query.filter_by(id=nouveau_prof_id, ecole_id=current_user.ecole_id).first()
                if not nouveau_prof:
                    flash("Nouveau professeur invalide pour cet etablissement.", "danger")
                    return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
                cours.professeur_id = nouveau_prof.id
                message = f"Professeur change pour {cours.nom} - {cours.classe.nom}."
            else:
                cours.professeur_id = professeur.id
                message = f"{professeur.prenom} {professeur.nom} affecte a {cours.nom} - {cours.classe.nom}."

            db.session.commit()
            current_app.log_correction(
                action="modification",
                description=f"Affectation enseignement {cours.nom} - {cours.classe.nom} ({annee_consultee.nom})",
                ecole_id=professeur.ecole_id,
                cible_type="cours",
                cible_id=cours.id,
                ancienne_valeur=str(ancienne_valeur),
                nouvelle_valeur=str(cours.professeur_id),
                niveau="info",
            )
            flash(message, "success")
            return redirect(url_for('main.assigner_classes_professeur', id=professeur.id))
        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'affectation de l'enseignement", "danger")
            current_app.logger.error(f"Erreur affectation enseignement: {e}")

    cours_professeur = [cours for cours in cours_annee if cours.professeur_id == professeur.id]
    cours_disponibles = [cours for cours in cours_annee if cours.professeur_id in (None, professeur.id)]
    classes_avec_matiere_prof = {
        cours.classe_id
        for cours in cours_annee
        if matiere_professeur
        and cours.classe_id is not None
        and (cours.nom or "").strip().lower() == matiere_professeur.strip().lower()
    }
    classes_sans_matiere_prof = [
        classe for classe in classes_annee
        if matiere_professeur
        and classe_est_ouverte(classe)
        and classe.id not in classes_avec_matiere_prof
    ]

    return render_template(
        'assigner_classes.html',
        professeur=professeur,
        professeurs=professeurs_ecole,
        cours_annee=cours_annee,
        cours_professeur=cours_professeur,
        cours_disponibles=cours_disponibles,
        classes_sans_matiere_prof=classes_sans_matiere_prof,
        matiere_professeur=matiere_professeur,
        annee_consultee=annee_consultee,
        est_archivee=est_archivee,
    )

@main.route("/mes_classes")
@login_required
@role_required('professeur')
def mes_classes():
    prof = current_user.professeur_rel
    if not prof:
        flash("Aucune information de professeur trouvée.", "warning")
        return redirect(url_for('main.index'))

    annee_consultee = get_annee_consultee(current_user.ecole_id)
    if not annee_consultee:
        return render_template("mes_classes.html", classes=[])

    classe_ids = [
        row.classe_id
        for row in Cours.query.with_entities(Cours.classe_id)
        .join(Classe, Classe.id == Cours.classe_id)
        .filter(
            Cours.professeur_id == prof.id,
            Cours.ecole_id == current_user.ecole_id,
            Cours.classe_id.isnot(None),
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
        )
        .distinct()
        .all()
    ]
    classes = (
        Classe.query.filter(
            Classe.ecole_id == current_user.ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
            Classe.id.in_(classe_ids),
        )
        .order_by(Classe.nom.asc())
        .all()
        if classe_ids
        else []
    )

    inscriptions = (
        Inscription.query.filter(
            Inscription.ecole_id == current_user.ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Inscription.classe_id.in_(classe_ids),
            Inscription.statut != 'desinscrit'
        )
        .options(db.joinedload(Inscription.eleve))
        .all()
    ) if classe_ids else []

    inscriptions_par_classe = {}
    for insc in inscriptions:
        inscriptions_par_classe.setdefault(insc.classe_id, []).append(insc)

    for classe in classes:
        classe.inscriptions_actives = inscriptions_par_classe.get(classe.id, [])

    return render_template("mes_classes.html", classes=classes)


@main.route("/mes_enseignements")
@login_required
@role_required("professeur")
def mes_enseignements():
    """Hub professeur : classes et cours autorises pour l'annee consultee."""
    professeur = current_user.professeur_rel
    if not professeur:
        flash("Aucune information de professeur trouvee.", "warning")
        return redirect(url_for("main.professeur_dashboard"))

    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    if not annee_consultee:
        return render_template(
            "mes_enseignements.html",
            classes_data=[],
            cours_data=[],
            annee_consultee=None,
        )

    cours_prof = (
        Cours.query
        .join(Classe, Classe.id == Cours.classe_id)
        .options(joinedload(Cours.classe), joinedload(Cours.notes), joinedload(Cours.absences))
        .filter(
            Cours.ecole_id == ecole_id,
            Cours.professeur_id == professeur.id,
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
        )
        .order_by(Classe.nom.asc(), Cours.nom.asc())
        .all()
    )

    cours_par_classe = {}
    for cours in cours_prof:
        if cours.classe_id:
            cours_par_classe.setdefault(cours.classe_id, []).append(cours)

    classe_ids = sorted(set(cours_par_classe.keys()))

    classes = (
        Classe.query
        .options(joinedload(Classe.niveau_scolaire))
        .filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee_consultee.id,
            Classe.id.in_(classe_ids),
        )
        .order_by(Classe.nom.asc())
        .all()
        if classe_ids
        else []
    )

    effectifs = {
        row.classe_id: row.total
        for row in db.session.query(Inscription.classe_id, db.func.count(Inscription.id).label("total"))
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Inscription.classe_id.in_(classe_ids),
            Inscription.statut == "inscrit",
        )
        .group_by(Inscription.classe_id)
        .all()
    } if classe_ids else {}

    absences_counts = {
        row.classe_id: row.total
        for row in db.session.query(Inscription.classe_id, db.func.count(Absence.id).label("total"))
        .join(Absence, Absence.inscription_id == Inscription.id)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Inscription.classe_id.in_(classe_ids),
            Absence.cours_id.in_([c.id for c in cours_prof]) if cours_prof else db.false(),
        )
        .group_by(Inscription.classe_id)
        .all()
    } if classe_ids and cours_prof else {}

    classes_data = []
    for classe in classes:
        mes_cours_classe = cours_par_classe.get(classe.id, [])
        classes_data.append({
            "classe": classe,
            "effectif": effectifs.get(classe.id, 0),
            "cours": mes_cours_classe,
            "absences_count": absences_counts.get(classe.id, 0),
        })

    cours_data = []
    for cours in cours_prof:
        cours_data.append({
            "cours": cours,
            "effectif": effectifs.get(cours.classe_id, 0),
            "notes_count": len(cours.notes) if getattr(cours, "notes", None) is not None else 0,
        })

    return render_template(
        "mes_enseignements.html",
        classes_data=classes_data,
        cours_data=cours_data,
        annee_consultee=annee_consultee,
    )
