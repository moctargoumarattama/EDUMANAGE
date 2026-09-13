from . import main
from .common import (
    abort,
    Absence,
    can_manage_absence,
    can_manage_cours,
    AbsenceForm,
    can_access_absence,
    can_access_cours,
    can_access_eleve,
    Classe,
    Cours,
    Eleve,
    Inscription,
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
from app.services.annees_scolaires import get_annee_consultee
from app.services.absences_annuelles import (
    absences_modifiables,
    get_absences_annee,
    get_cours_choices_absences,
    get_eleves_choices_absences,
    statut_annee_absences,
    verifier_mutation_absence,
)


def _ecole_id_courante():
    return getattr(current_user, "ecole_id", None)


def _remplir_choix_absence(form, ecole_id, annee):
    form.eleve_id.choices = get_eleves_choices_absences(ecole_id, annee, current_user)
    form.cours_id.choices = get_cours_choices_absences(ecole_id, annee, current_user)


@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur', 'parent')
def absences():
    form = AbsenceForm()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    _remplir_choix_absence(form, ecole_id, annee_consultee)

    if form.validate_on_submit():
        if not can_mutate:
            flash(message_annee or "Les absences ne peuvent pas etre modifiees pour cette annee.", "warning")
            return redirect(url_for('main.absences'))

        try:
            eleve, cours, _inscription, error = verifier_mutation_absence(
                ecole_id,
                annee_consultee,
                current_user,
                form.eleve_id.data,
                form.cours_id.data,
                form.date_absence.data,
            )
            if error:
                flash(error, "danger")
                return redirect(url_for('main.absences'))

            nouvelle_absence = Absence(
                date_absence=form.date_absence.data,
                motif=form.motif.data,
                justifiee=form.justifiee.data,
                eleve_id=form.eleve_id.data,
                cours_id=form.cours_id.data,
                ecole_id=ecole_id,
                inscription_id=_inscription.id if _inscription else None,
            )
            db.session.add(nouvelle_absence)
            db.session.commit()

            if eleve and eleve.email_parent and cours:
                sujet = f"Absence de {eleve.prenom} {eleve.nom}"
                message = f"""Bonjour,
Nous vous informons que {eleve.prenom} {eleve.nom} a ete absent(e) le {form.date_absence.data.strftime('%d/%m/%Y')}.
Motif: {form.motif.data}
Cours: {cours.nom}
Statut: {'Justifiee' if form.justifiee.data else 'Non justifiee'}

Cordialement,
L'equipe pedagogique"""
                try:
                    envoyer_email(eleve.email_parent, sujet, message)
                except Exception as e:
                    current_app.logger.error(f"Erreur envoi email absence: {e}")

            flash('Absence enregistree avec succes', 'success')
            return redirect(url_for('main.absences'))

        except Exception as e:
            db.session.rollback()
            flash("Erreur lors de l'enregistrement de l'absence.", "danger")
            current_app.logger.error(f"Erreur ajout absence: {e}")

    absences_list = get_absences_annee(ecole_id, annee_consultee, current_user)

    total = len(absences_list)
    start = (page - 1) * per_page
    end = start + per_page
    absences_paginated = absences_list[start:end]

    absences_justifiees = sum(1 for a in absences_list if a.justifiee)
    absences_non_justifiees = total - absences_justifiees
    show_form = can_mutate

    return render_template(
        'absences.html',
        form=form,
        absences=absences_paginated,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        page=page,
        per_page=per_page,
        total=total,
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
    )
@main.route('/absences/export_excel')
@login_required
@role_required('admin')
def export_absences_excel():
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    absences = get_absences_annee(ecole_id, annee_consultee, current_user)

    data = {
        'Date': [a.date_absence.strftime('%d/%m/%Y') for a in absences],
        'Eleve': [f"{a.eleve.prenom} {a.eleve.nom}" for a in absences],
        'Classe': [a.annee_classe.nom if getattr(a, 'annee_classe', None) else 'Sans classe' for a in absences],
        'Motif': [a.motif for a in absences],
        'Justifiee': ['Oui' if a.justifiee else 'Non' for a in absences],
        'Annee scolaire': [annee_consultee.nom if annee_consultee else '' for _ in absences],
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Absences', index=False)
    output.seek(0)

    suffix = f"_{annee_consultee.nom}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"liste_absences{suffix}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
@main.route('/absences/edit/<int:absence_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
def edit_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    if absence.ecole_id != ecole_id:
        abort(403)

    _eleve, _cours, _inscription, access_error = verifier_mutation_absence(
        ecole_id,
        annee_consultee,
        current_user,
        absence.eleve_id,
        absence.cours_id,
        absence.date_absence,
        absence=absence,
    )
    if access_error:
        flash(access_error, "warning")
        return redirect(url_for('main.absences'))

    form = AbsenceForm(obj=absence)
    _remplir_choix_absence(form, ecole_id, annee_consultee)
    form.eleve_id.data = absence.eleve_id
    form.cours_id.data = absence.cours_id

    if form.validate_on_submit():
        eleve, cours, _inscription, error = verifier_mutation_absence(
            ecole_id,
            annee_consultee,
            current_user,
            form.eleve_id.data,
            form.cours_id.data,
            form.date_absence.data,
            absence=absence,
        )
        if error:
            flash(error, "danger")
            return redirect(url_for('main.absences'))

        absence.eleve_id = eleve.id
        absence.cours_id = cours.id if cours else None
        absence.date_absence = form.date_absence.data
        absence.motif = form.motif.data
        absence.justifiee = form.justifiee.data

        db.session.commit()
        flash("Absence mise a jour avec succes.", "success")
        return redirect(url_for('main.absences'))

    return render_template('edit_absence.html', form=form, annee_consultee=annee_consultee)


@main.route('/absences/delete/<int:absence_id>', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
def delete_absence(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    _eleve, _cours, _inscription, access_error = verifier_mutation_absence(
        ecole_id,
        annee_consultee,
        current_user,
        absence.eleve_id,
        absence.cours_id,
        absence.date_absence,
        absence=absence,
    )
    if access_error:
        flash(access_error, "warning")
        return redirect(url_for('main.absences'))

    try:
        db.session.delete(absence)
        db.session.commit()
        flash("Absence supprimee avec succes.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")

    return redirect(url_for('main.absences'))
@main.route("/presence", methods=["GET", "POST"])
@login_required
@role_required("professeur")
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
        eleve_ids_classe = [
            row[0]
            for row in db.session.query(Inscription.eleve_id)
            .filter_by(classe_id=classe_id)
            .all()
        ]
        historique = Presence.query.join(Eleve)\
            .filter(Presence.eleve_id.in_(eleve_ids_classe))\
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
