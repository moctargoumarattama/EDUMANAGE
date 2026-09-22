from . import main
from types import SimpleNamespace
import pandas as pd
from .common import (
    abort,
    Absence,
    AbsenceForm,
    Classe,
    Cours,
    Eleve,
    Inscription,
    current_app,
    current_user,
    date,
    datetime,
    db,
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
from flask import jsonify
from app.services.annees_scolaires import get_annee_consultee
from app.services.structure_annuelle import get_niveaux_annee
from app.services.absences_annuelles import (
    absences_modifiables,
    get_absences_annee,
    get_classes_absences,
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


def _niveaux_depuis_classes(classes):
    niveaux_par_id = {}
    for classe in classes:
        niveau = getattr(classe, "niveau_scolaire", None)
        if niveau and niveau.id not in niveaux_par_id:
            niveaux_par_id[niveau.id] = niveau
            continue
        niveau_nom = (getattr(classe, "niveau", None) or "").strip()
        if niveau_nom and f"legacy:{niveau_nom}" not in niveaux_par_id:
            niveaux_par_id[f"legacy:{niveau_nom}"] = SimpleNamespace(id=niveau_nom, nom=niveau_nom, ordre=999)
    return sorted(niveaux_par_id.values(), key=lambda niveau: (niveau.ordre, niveau.nom))


@main.route('/absences', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
def absences():
    if request.method == 'POST':
        flash("L'enregistrement initial des absences est réservé aux professeurs lors de la prise d'appel.", "warning")
        return redirect(url_for('main.absences'))

    page = request.args.get('page', 1, type=int)
    per_page = 50
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    absences_list = get_absences_annee(ecole_id, annee_consultee, current_user)

    search = (request.args.get('search') or request.args.get('q') or '').strip().lower()
    classe_id = request.args.get('classe_id', type=int) or request.args.get('classe', type=int)
    niveau_param = (request.args.get('niveau') or request.args.get('niveau_id') or '').strip()
    date_debut_str = request.args.get('date_debut')
    date_fin_str = request.args.get('date_fin')
    justifiee_param = request.args.get('justifiee')

    # Filtrage
    filtrees = []
    for a in absences_list:
        if classe_id:
            c = getattr(a, 'annee_classe', None)
            if not c or c.id != classe_id:
                continue
        if niveau_param:
            c = getattr(a, 'annee_classe', None)
            if not c:
                continue
            if str(niveau_param).isdigit():
                if getattr(c, 'niveau_id', None) != int(niveau_param) and str(c.niveau) != str(niveau_param):
                    continue
            elif str(c.niveau or '').strip().lower() != niveau_param.lower():
                continue
        if search:
            eleve_str = f"{a.eleve.prenom} {a.eleve.nom}".lower() if a.eleve else ""
            matricule = (getattr(a.eleve, 'code_parent', '') or '').lower() if a.eleve else ""
            cours_str = (a.cours.nom if a.cours else "").lower()
            motif_str = (a.motif or "").lower()
            if search not in eleve_str and search not in matricule and search not in cours_str and search not in motif_str:
                continue
        if justifiee_param in ('1', 'true', 'yes', 'justifiee'):
            if not a.justifiee:
                continue
        elif justifiee_param in ('0', 'false', 'no', 'non-justifiee'):
            if a.justifiee:
                continue
        if date_debut_str:
            try:
                d_deb = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
                if a.date_absence and a.date_absence < d_deb:
                    continue
            except ValueError:
                pass
        if date_fin_str:
            try:
                d_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
                if a.date_absence and a.date_absence > d_fin:
                    continue
            except ValueError:
                pass
        filtrees.append(a)

    total = len(filtrees)
    start = (page - 1) * per_page
    end = start + per_page
    absences_paginated = filtrees[start:end]

    absences_justifiees = sum(1 for a in filtrees if a.justifiee)
    absences_non_justifiees = total - absences_justifiees
    show_form = False

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': total,
            'absences': [
                {
                    'id': a.id,
                    'eleve_id': a.eleve_id,
                    'eleve_nom': f"{a.eleve.prenom} {a.eleve.nom}" if a.eleve else "",
                    'classe_nom': a.annee_classe.nom if getattr(a, 'annee_classe', None) else "",
                    'cours_nom': a.cours.nom if a.cours else "",
                    'date': a.date_absence.strftime('%Y-%m-%d') if a.date_absence else "",
                    'motif': a.motif or "",
                    'justifiee': bool(a.justifiee)
                }
                for a in absences_paginated
            ]
        })

    classes = get_classes_absences(ecole_id, annee_consultee, current_user)
    if current_user.role == "professeur":
        niveaux_annee = _niveaux_depuis_classes(classes)
    else:
        niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if (ecole_id and annee_consultee) else []

    return render_template(
        'absences.html',
        absences=absences_paginated,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        show_form=show_form,
        page=page,
        per_page=per_page,
        total=total,
        classes=classes,
        niveaux_annee=niveaux_annee,
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
        search=search,
        classe_id=classe_id,
        niveau=niveau_param,
        date_debut=date_debut_str,
        date_fin=date_fin_str,
        justifiee=justifiee_param
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
@role_required("professeur", "admin")
def presence():
    flash("KLASORA gère uniquement les absences scolaires.", "info")
    return redirect(url_for("main.absences"))


@main.route("/absences/appel", methods=["GET", "POST"])
@login_required
@role_required("professeur")
def faire_appel():
    ecole_id = _ecole_id_courante()
    annee_consultee = get_annee_consultee(ecole_id)
    can_mutate = absences_modifiables(annee_consultee, current_user)
    message_annee = statut_annee_absences(annee_consultee)

    classe_id = request.values.get("classe_id", type=int)
    cours_id = request.values.get("cours_id", type=int)
    date_appel_str = request.values.get("date_appel") or date.today().isoformat()
    try:
        date_appel = datetime.strptime(date_appel_str, "%Y-%m-%d").date()
    except ValueError:
        date_appel = date.today()

    if not annee_consultee:
        flash("Aucune annee scolaire active pour faire l'appel.", "warning")
        return redirect(url_for("main.absences"))

    cours = None
    classe = None
    if cours_id:
        cours = (
            Cours.query
            .join(Classe, Classe.id == Cours.classe_id)
            .filter(
                Cours.id == cours_id,
                Cours.ecole_id == ecole_id,
                Classe.ecole_id == ecole_id,
                Classe.annee_scolaire_id == annee_consultee.id,
            )
            .first()
        )
        if not cours:
            abort(404)
        classe = cours.classe
        classe_id = classe.id if classe else classe_id

    if classe_id and not classe:
        classe = Classe.query.filter_by(
            id=classe_id,
            ecole_id=ecole_id,
            annee_scolaire_id=annee_consultee.id,
        ).first_or_404()

    professeur = getattr(current_user, "professeur_rel", None)
    if not professeur:
        abort(403)
    if cours and cours.professeur_id != professeur.id:
        abort(403)
    if classe and not cours:
        cours = Cours.query.filter_by(
            classe_id=classe.id,
            professeur_id=professeur.id,
            ecole_id=ecole_id,
        ).order_by(Cours.nom.asc()).first()
        if not cours:
            abort(403)

    cours_disponibles = []
    if classe:
        cours_query = Cours.query.filter_by(
            classe_id=classe.id,
            ecole_id=ecole_id,
            professeur_id=professeur.id,
        )
        cours_disponibles = cours_query.order_by(Cours.nom.asc()).all()
        if not cours and cours_disponibles:
            cours = cours_disponibles[0]
            cours_id = cours.id

    if not classe or not cours:
        flash("Selectionnez une classe et un cours pour faire l'appel.", "warning")
        return redirect(url_for("main.absences"))

    inscriptions = (
        Inscription.query
        .options(selectinload(Inscription.eleve))
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Inscription.classe_id == classe.id,
            Inscription.statut == "inscrit",
        )
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    inscription_ids = [ins.id for ins in inscriptions]

    absences_existantes = (
        Absence.query
        .filter(
            Absence.ecole_id == ecole_id,
            Absence.cours_id == cours.id,
            Absence.date_absence == date_appel,
            Absence.inscription_id.in_(inscription_ids),
        )
        .all()
        if inscription_ids
        else []
    )
    absences_par_inscription = {a.inscription_id: a for a in absences_existantes}

    if request.method == "POST":
        if not can_mutate:
            flash(message_annee or "Les absences ne peuvent pas etre modifiees pour cette annee.", "warning")
            return redirect(url_for("main.faire_appel", classe_id=classe.id, cours_id=cours.id, date_appel=date_appel.isoformat()))

        absent_ids = {
            int(raw_id)
            for raw_id in request.form.getlist("absent_inscription_ids")
            if raw_id.isdigit()
        }
        absent_ids = absent_ids & set(inscription_ids)

        try:
            for absence in list(absences_existantes):
                if absence.inscription_id not in absent_ids:
                    db.session.delete(absence)

            for ins in inscriptions:
                if ins.id in absent_ids and ins.id not in absences_par_inscription:
                    db.session.add(Absence(
                        date_absence=date_appel,
                        motif="Absence signalee pendant l'appel",
                        justifiee=False,
                        eleve_id=ins.eleve_id,
                        cours_id=cours.id,
                        ecole_id=ecole_id,
                        inscription_id=ins.id,
                    ))

            db.session.commit()
            flash("Appel enregistre. Aucune presence n'a ete creee.", "success")
            return redirect(url_for("main.faire_appel", classe_id=classe.id, cours_id=cours.id, date_appel=date_appel.isoformat()))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Erreur enregistrement appel professeur: %s", exc)
            flash("Erreur lors de l'enregistrement de l'appel.", "danger")

    absents_count = len(absences_par_inscription)
    effectif = len(inscriptions)

    return render_template(
        "faire_appel.html",
        classe=classe,
        cours=cours,
        cours_disponibles=cours_disponibles,
        inscriptions=inscriptions,
        absences_par_inscription=absences_par_inscription,
        date_appel=date_appel,
        effectif=effectif,
        absents_count=absents_count,
        presents_count=max(effectif - absents_count, 0),
        annee_consultee=annee_consultee,
        can_mutate=can_mutate,
        message_annee=message_annee,
    )
