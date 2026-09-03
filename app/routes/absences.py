from . import main
from .common import (
    Absence,
    AbsenceForm,
    can_access_absence,
    can_access_cours,
    can_access_eleve,
    Classe,
    Cours,
    Eleve,
    Presence,
    ajouter_ecole_id,
    current_app,
    current_user,
    date,
    datetime,
    db,
    envoyer_email,
    filtre_par_ecole,
    flash,
    io,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    selectinload,
    send_file,
    url_for,
)
import pandas as pd


@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'enseignant', 'parent')
def absences():
    form = AbsenceForm()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # --- Fonction utilitaire pour transformer Query en liste ---
    def to_list(query_or_list):
        if hasattr(query_or_list, 'all'):
            return query_or_list.all()
        return list(query_or_list)

    # --- Choix des élèves selon le rôle ---
    if current_user.role == 'parent':
        enfants = to_list(filtre_par_ecole(current_user.enfants, Eleve))
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}
    elif current_user.role in ('enseignant', 'professeur'):
        professeur = getattr(current_user, 'professeur_rel', None)
        classe_ids = [c.id for c in professeur.classes_assignees.all()] if professeur else []
        enfants = (
            filtre_par_ecole(Eleve.query.options(selectinload(Eleve.classe)), Eleve)
            .filter(Eleve.classe_id.in_(classe_ids))
            .all()
        ) if classe_ids else []
        enfants.sort(key=lambda e: ((e.classe.nom if e.classe else ""), e.nom))
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
    else:
        enfants_query = filtre_par_ecole(
            Eleve.query.options(selectinload(Eleve.classe)), Eleve
        )
        enfants = to_list(enfants_query)
        # --- Tri par nom de classe et nom de l'élève en Python (compatible SQLite) ---
        enfants.sort(key=lambda e: ((e.classe.nom if e.classe else ""), e.nom))
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]

    # --- Choix des cours ---
    cours_query = filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours)
    if current_user.role in ('enseignant', 'professeur'):
        professeur = getattr(current_user, 'professeur_rel', None)
        cours_query = cours_query.filter(Cours.professeur_id == professeur.id) if professeur else cours_query.filter(False)
    cours_list = to_list(cours_query)
    form.cours_id.choices = [(c.id, c.nom) for c in cours_list]

    # --- Gestion du formulaire POST ---
    if form.validate_on_submit():
        if current_user.role == 'parent':
            flash("Vous n'êtes pas autorisé à déclarer des absences.", "danger")
            return redirect(url_for('main.absences'))

        try:
            eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
            cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
            if not can_access_eleve(eleve) or (cours and not can_access_cours(cours)):
                flash("AccÃ¨s non autorisÃ© pour cet Ã©lÃ¨ve ou ce cours.", "danger")
                return redirect(url_for('main.absences'))

            nouvelle_absence = Absence(
                date_absence=form.date_absence.data,
                motif=form.motif.data,
                justifiee=form.justifiee.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data
            )
            ajouter_ecole_id(nouvelle_absence)
            db.session.add(nouvelle_absence)
            db.session.commit()

            # --- Notification email ---
            if eleve and eleve.email_parent and cours:
                sujet = f"Absence de {eleve.prenom} {eleve.nom}"
                message = f"""Bonjour,
Nous vous informons que {eleve.prenom} {eleve.nom} a été absent(e) le {form.date_absence.data.strftime('%d/%m/%Y')}.
Motif: {form.motif.data}
Cours: {cours.nom}
Statut: {'Justifiée' if form.justifiee.data else 'Non justifiée'}

Cordialement,
L'équipe pédagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email absence: {e}")

            flash('Absence enregistrée avec succès', 'success')
            return redirect(url_for('main.absences'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'enregistrement de l'absence.", "danger")
            current_app.logger.error(f"Erreur ajout absence: {e}")

    # --- Filtrage et tri des absences ---
    if current_user.role == 'parent':
        enfants_ids = [e.id for e in enfants]
        absences_query = filtre_par_ecole(
            Absence.query.filter(Absence.eleve_id.in_(enfants_ids))
                         .options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
        )
    elif current_user.role in ('enseignant', 'professeur'):
        eleve_ids = [e.id for e in enfants]
        absences_query = filtre_par_ecole(
            Absence.query.filter(Absence.eleve_id.in_(eleve_ids))
                         .options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
        )
    else:
        absences_query = filtre_par_ecole(
            Absence.query.options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
        )

    absences_list = to_list(absences_query)
    # Tri en Python par classe puis nom élève
    absences_list.sort(key=lambda a: ((a.eleve.classe.nom if a.eleve.classe else ""), a.eleve.nom, a.date_absence), reverse=True)

    # --- Pagination manuelle pour SQLite (compatible) ---
    total = len(absences_list)
    start = (page - 1) * per_page
    end = start + per_page
    absences_paginated = absences_list[start:end]

    # --- Statistiques ---
    absences_justifiees = sum(1 for a in absences_list if a.justifiee)
    absences_non_justifiees = total - absences_justifiees
    show_form = current_user.role in ['admin', 'enseignant', 'professeur']

    return render_template(
        'absences.html',
        form=form,
        absences=absences_paginated,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        page=page,
        per_page=per_page,
        total=total
    )

@main.route('/absences/export_excel')
@login_required
@role_required('admin')
def export_absences_excel():
    absences = filtre_par_ecole(
        Absence.query.options(selectinload(Absence.eleve).selectinload(Eleve.classe)), Absence
    ).all()
    
    data = {
        'Date': [a.date_absence.strftime('%d/%m/%Y') for a in absences],
        'Élève': [f"{a.eleve.prenom} {a.eleve.nom}" for a in absences],
        'Classe': [a.eleve.classe.nom if a.eleve.classe else 'Sans classe' for a in absences],
        'Motif': [a.motif for a in absences],
        'Justifiée': ['Oui' if a.justifiee else 'Non' for a in absences]
    }
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Absences', index=False)
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="liste_absences.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/absences/edit/<int:absence_id>', methods=['GET', 'POST'])
@login_required
def edit_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    if not can_access_absence(absence) or current_user.role == 'parent':
        flash("AccÃ¨s non autorisÃ© Ã  cette absence.", "danger")
        return redirect(url_for('main.absences'))
    form = AbsenceForm(obj=absence)
    
    # Remplir les choix des élèves
    if current_user.role == 'parent':
        enfants = Eleve.query.filter_by(parent_id=current_user.id).all()
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in enfants
        ]
        form.eleve_id.render_kw = {'disabled': True} if len(enfants) == 1 else {}
    elif current_user.role in ('enseignant', 'professeur'):
        professeur = getattr(current_user, 'professeur_rel', None)
        classe_ids = [c.id for c in professeur.classes_assignees.all()] if professeur else []
        eleves = filtre_par_ecole(Eleve.query.join(Classe, isouter=True), Eleve).filter(Eleve.classe_id.in_(classe_ids)).order_by(Classe.nom, Eleve.nom).all() if classe_ids else []
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in eleves
        ]
    else:
        eleves = filtre_par_ecole(Eleve.query.join(Classe, isouter=True), Eleve).order_by(Classe.nom, Eleve.nom).all()
        form.eleve_id.choices = [
            (e.id, f"{e.prenom} {e.nom} - {e.classe.nom if e.classe else 'Sans classe'}")
            for e in eleves
        ]
    
    # Remplir les choix des cours
    cours_query = filtre_par_ecole(Cours.query.order_by(Cours.nom), Cours)
    if current_user.role in ('enseignant', 'professeur'):
        professeur = getattr(current_user, 'professeur_rel', None)
        cours_query = cours_query.filter(Cours.professeur_id == professeur.id) if professeur else cours_query.filter(False)
    form.cours_id.choices = [(c.id, c.nom) for c in cours_query.all()]

    # Mettre à jour la sélection actuelle
    form.eleve_id.data = absence.eleve_id
    form.cours_id.data = absence.cours_id

    if form.validate_on_submit():
        eleve = filtre_par_ecole(Eleve.query.filter_by(id=form.eleve_id.data), Eleve).first()
        cours = filtre_par_ecole(Cours.query.filter_by(id=form.cours_id.data), Cours).first()
        if not can_access_eleve(eleve) or (cours and not can_access_cours(cours)):
            flash("AccÃ¨s non autorisÃ© pour cet Ã©lÃ¨ve ou ce cours.", "danger")
            return redirect(url_for('main.absences'))
        absence.eleve_id = form.eleve_id.data
        absence.cours_id = form.cours_id.data
        absence.date_absence = form.date_absence.data
        absence.motif = form.motif.data
        absence.justifiee = form.justifiee.data

        db.session.commit()
        flash("Absence mise à jour avec succès.", "success")
        return redirect(url_for('main.absences'))
    
    return render_template('edit_absence.html', form=form)

@main.route('/absences/delete/<int:absence_id>', methods=['POST'])
@login_required
def delete_absence(absence_id):
    try:
        absence = Absence.query.get_or_404(absence_id)
        if not can_access_absence(absence) or current_user.role == 'parent':
            flash("AccÃ¨s non autorisÃ© Ã  cette absence.", "danger")
            return redirect(url_for('main.absences'))
        db.session.delete(absence)
        db.session.commit()
        flash("Absence supprimée avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for('main.absences'))

@main.route("/presence", methods=["GET", "POST"])
@login_required
@role_required("enseignant")
def presence():

    prof = current_user.professeur_rel
    if not prof:
        flash("Aucun profil professeur trouvé.", "danger")
        return redirect(url_for("main.index"))

    classes = prof.classes_assignees

    # -----------------------------
    # 1) Sélection classe
    # -----------------------------
    classe_id = request.values.get("classe_id", type=int)
    selected_classe = None
    eleves = []

    if classe_id:
        selected_classe = next((c for c in classes if c.id == classe_id), None)
        if selected_classe:
            eleves = selected_classe.eleves

    # -----------------------------
    # 2) Sélection date
    # -----------------------------
    date_str = request.values.get("date") or date.today().isoformat()

    try:
        date_selectionnee = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        date_selectionnee = date.today()

    heure_selectionnee = request.form.get("heure_presence", "")
    matiere = request.form.get("matiere", "")

    # -----------------------------
    # 3) Présences existantes
    # -----------------------------
    presences_existantes = {}
    if eleves:
        for e in eleves:
            p = Presence.query.filter_by(
                eleve_id=e.id,
                date=date_selectionnee
            ).first()
            presences_existantes[e.id] = p.statut if p else None

    # -----------------------------
    # 4) POST : Enregistrement
    # -----------------------------
    if request.method == "POST" and eleves:

        date_p_str = request.form.get("date_presence")
        heure_p_str = request.form.get("heure_presence")
        matiere = request.form.get("matiere")

        # Conversion obligatoire
        try:
            date_p = datetime.strptime(date_p_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            flash("Date invalide.", "danger")
            return redirect(request.url)

        # L'heure reste en string (pas besoin time())
        heure_p = heure_p_str  

        with db.session.no_autoflush:       # 🔥 corrige l'erreur SQLite
            for eleve in eleves:
                statut = request.form.get(f"eleve_{eleve.id}")
                if not statut:
                    continue

                ligne = Presence.query.filter_by(
                    eleve_id=eleve.id,
                    date=date_p
                ).first()

                if not ligne:
                    ligne = Presence(
                        eleve_id=eleve.id,
                        date=date_p,
                        heure=heure_p,
                        matiere=matiere,
                        statut=statut
                    )
                    db.session.add(ligne)
                else:
                    ligne.statut = statut
                    ligne.matiere = matiere
                    ligne.heure = heure_p

        db.session.commit()
        flash("Présences enregistrées.", "success")

        return redirect(url_for("main.presence",
                                classe_id=classe_id,
                                date=date_p_str))

    # -----------------------------
    # 5) Historique du jour
    # -----------------------------
    historique = []
    if classe_id:
        historique = Presence.query.join(Eleve)\
            .filter(Eleve.classe_id == classe_id)\
            .filter(Presence.date == date_selectionnee)\
            .all()

    # -----------------------------
    # 6) Render
    # -----------------------------
    return render_template(
        "presence.html",
        classes=classes,
        classe_id=classe_id,
        eleves=eleves,
        presences_existantes=presences_existantes,
        date_selectionnee=date_selectionnee.isoformat(),
        heure_selectionnee=heure_selectionnee,
        matiere=matiere,
        historique=historique
    )
