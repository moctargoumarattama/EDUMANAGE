import io
import pandas as pd
from types import SimpleNamespace
from datetime import datetime
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from . import main
from .common import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    NoteForm,
    Professeur,
    can_manage_note,
    db,
    get_ecole_courante,
    role_required,
)
from app.services.annees_scolaires import get_annee_consultee
from app.services.notes_annuelles import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    calculer_statistiques_notes,
    creer_note,
    get_classes_notes,
    get_cours_annee,
    get_cours_choices_notes,
    get_eleves_choices_notes,
    get_inscriptions_notes,
    get_notes_annee,
    modifier_note as service_modifier_note,
    notes_modifiables,
    saisir_notes_classe as service_saisir_notes_classe,
    statut_annee_notes,
    supprimer_note as service_supprimer_note,
)



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


@main.route('/notes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur', 'parent')
def notes():
    ecole_id = current_user.ecole_id

    # ------------------- Contexte annuel unique (Règle 2C-5D) -------------------
    annee_consultee = get_annee_consultee(ecole_id)
    message_annee = statut_annee_notes(annee_consultee)
    peut_modifier = notes_modifiables(annee_consultee, current_user)

    # ------------------- Sécurité backend : Saisie de note réservée aux professeurs -------------------
    if request.method == 'POST':
        if current_user.role != 'professeur':
            flash("La saisie des notes est réservée aux professeurs.", "warning")
            return redirect(url_for('main.notes'))

    form = NoteForm() if current_user.role == 'professeur' else None

    # ------------------- Choix élèves et cours pour le professeur -------------------
    if form and annee_consultee:
        eleve_choices = get_eleves_choices_notes(ecole_id, annee_consultee, user=current_user)
        form.eleve_id.choices = eleve_choices or [(0, "--- Aucun élève disponible ---")]

        cours_choices = get_cours_choices_notes(ecole_id, annee_consultee, user=current_user)
        form.cours_id.choices = cours_choices or [(0, "--- Aucun cours disponible ---")]

        if hasattr(form, 'annee_id'):
            form.annee_id.choices = [(annee_consultee.id, annee_consultee.nom)]
            form.annee_id.data = annee_consultee.id
    elif form:
        form.eleve_id.choices = [(0, "--- Aucun élève disponible ---")]
        form.cours_id.choices = [(0, "--- Aucun cours disponible ---")]
        if hasattr(form, 'annee_id'):
            form.annee_id.choices = [(0, "--- Aucune année disponible ---")]

    # ------------------- Ajout d'une note (POST professeur) -------------------
    if form and form.validate_on_submit():
        if not peut_modifier:
            if annee_consultee and annee_consultee.statut == 'archivee':
                flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
            elif annee_consultee and annee_consultee.statut == 'planifiee':
                flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
            else:
                flash("Action non autorisée pour cette année scolaire.", "danger")
            return redirect(url_for('main.notes'))

        nouvelle_note, err = creer_note(
            ecole_id=ecole_id,
            annee=annee_consultee,
            user=current_user,
            eleve_id=form.eleve_id.data,
            cours_id=form.cours_id.data,
            valeur=form.valeur.data,
            coefficient=form.coefficient.data,
            type_evaluation=form.type_evaluation.data,
            periode=form.periode.data,
            date_evaluation=datetime.utcnow(),
        )

        if err:
            flash(err, "danger")
        else:
            flash("Note ajoutée avec succès", "success")
        return redirect(url_for('main.notes'))

    # ------------------- Récupération des notes et statistiques -------------------
    toutes_notes = get_notes_annee(
        ecole_id=ecole_id,
        annee=annee_consultee,
        user=current_user,
    )

    # ------------------- Filtres de recherche temps réel (Phase 5F) -------------------
    search = (request.args.get('search') or request.args.get('q') or '').strip()
    classe_id = request.args.get('classe_id', type=int)
    cours_id = request.args.get('cours_id', type=int)
    eleve_id = request.args.get('eleve_id', type=int)
    niveau_param = (request.args.get('niveau') or request.args.get('niveau_id') or '').strip()
    periode = (request.args.get('periode') or '').strip()
    type_evaluation = (request.args.get('type_evaluation') or '').strip()

    notes_filtrees = toutes_notes
    if periode:
        notes_filtrees = [n for n in notes_filtrees if (n.periode or '').strip().lower() == periode.lower()]
    if type_evaluation:
        notes_filtrees = [n for n in notes_filtrees if (n.type_evaluation or '').strip().lower() == type_evaluation.lower()]
    if cours_id:
        notes_filtrees = [n for n in notes_filtrees if n.cours_id == cours_id]
    if eleve_id:
        notes_filtrees = [n for n in notes_filtrees if n.eleve_id == eleve_id]
    if classe_id:
        def note_match_classe(n):
            if n.inscription and n.inscription.classe_id == classe_id:
                return True
            if n.cours and n.cours.classe_id == classe_id:
                return True
            if n.eleve and n.eleve.classe_id == classe_id:
                return True
            return False
        notes_filtrees = [n for n in notes_filtrees if note_match_classe(n)]
    if niveau_param:
        def note_match_niveau(n):
            cl = (n.inscription.classe if n.inscription else None) or (n.cours.classe if n.cours else None) or (n.eleve.classe if n.eleve else None)
            if not cl:
                return False
            if str(niveau_param).isdigit():
                return getattr(cl, 'niveau_id', None) == int(niveau_param) or str(cl.niveau) == str(niveau_param)
            return str(cl.niveau or '').strip().lower() == niveau_param.lower()
        notes_filtrees = [n for n in notes_filtrees if note_match_niveau(n)]
    if search:
        s_lower = search.lower()
        def note_match_search(n):
            nom_eleve = f"{n.eleve.prenom} {n.eleve.nom}".lower() if n.eleve else ""
            nom_cours = n.cours.nom.lower() if n.cours else ""
            matricule = (n.eleve.code_parent or "").lower() if n.eleve else ""
            return s_lower in nom_eleve or s_lower in nom_cours or s_lower in matricule
        notes_filtrees = [n for n in notes_filtrees if note_match_search(n)]

    stats = calculer_statistiques_notes(notes_filtrees)

    classes_list = get_classes_notes(ecole_id, annee_consultee, user=current_user)
    if current_user.role == "professeur":
        niveaux_annee = _niveaux_depuis_classes(classes_list)
    else:
        from app.services.structure_annuelle import get_niveaux_annee
        niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if annee_consultee else []

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'total': len(notes_filtrees),
            'moyenne_generale': stats["moyenne_generale"],
            'taux_reussite': stats["taux_reussite"],
            'notes': [
                {
                    'id': n.id,
                    'valeur': n.valeur,
                    'coefficient': n.coefficient,
                    'type_evaluation': n.type_evaluation,
                    'periode': n.periode,
                    'eleve_id': n.eleve_id,
                    'eleve_nom': f"{n.eleve.prenom} {n.eleve.nom}" if n.eleve else "",
                    'cours_id': n.cours_id,
                    'cours_nom': n.cours.nom if n.cours else "",
                    'classe_id': (n.inscription.classe_id if n.inscription else (n.cours.classe_id if n.cours else None)),
                    'classe_nom': (n.inscription.classe.nom if (n.inscription and n.inscription.classe) else (n.cours.classe.nom if (n.cours and n.cours.classe) else "Sans classe")),
                } for n in notes_filtrees
            ]
        })

    # Inscriptions pour accordéons / structure annuelle
    inscriptions = get_inscriptions_notes(ecole_id, annee_consultee, user=current_user)
    eleve_classe_map = {ins.eleve_id: ins.classe_id for ins in inscriptions if ins.eleve_id and ins.classe_id}

    # Extraction des élèves uniques pour compatibilité templates
    eleves_uniques = []
    seen_eleves = set()
    for ins in inscriptions:
        if ins.eleve and ins.eleve.id not in seen_eleves:
            seen_eleves.add(ins.eleve.id)
            eleves_uniques.append(ins.eleve)

    tous_les_cours = get_cours_annee(ecole_id, annee_consultee, user=current_user)
    from collections import defaultdict
    from app.services.evaluations import (
        calculer_completude_inscription,
        preparer_dossier_notes_eleve,
    )

    notes_par_eleve = defaultdict(list)
    for n in notes_filtrees:
        if n.eleve_id:
            notes_par_eleve[n.eleve_id].append(n)

    dossiers_notes_par_eleve = {
        e_id: preparer_dossier_notes_eleve(e_notes)
        for e_id, e_notes in notes_par_eleve.items()
    }

    # Calcul de la complétude pédagogique par élève pour l'année et la période consultées
    periode_cible = periode if periode else None
    if not periode_cible and annee_consultee and ecole_id:
        from app.models import PeriodeBulletin
        p_active = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee_consultee.id,
            periode_active=True
        ).first()
        if p_active:
            periode_cible = p_active.nom

    completude_par_eleve = {}
    if annee_consultee and ecole_id:
        for ins in inscriptions:
            if ins.eleve_id:
                completude_par_eleve[ins.eleve_id] = calculer_completude_inscription(
                    ecole_id=ecole_id,
                    annee_id=annee_consultee.id,
                    inscription=ins,
                    periode=periode_cible,
                    periode_publiee=False,
                )

    return render_template(
        'notes.html',
        form=form if peut_modifier else None,
        notes=notes_filtrees,
        dossiers_notes_par_eleve=dossiers_notes_par_eleve,
        completude_par_eleve=completude_par_eleve,
        moyenne_generale=stats["moyenne_generale"],
        taux_reussite=stats["taux_reussite"],
        matieres_evaluees=stats["matieres_evaluees"],
        eleves=eleves_uniques,
        inscriptions=inscriptions,
        tous_les_cours=tous_les_cours,
        classes=classes_list,
        niveaux_annee=niveaux_annee,
        classe_id=classe_id,
        cours_id=cours_id,
        eleve_id=eleve_id,
        niveau_id=niveau_param,
        periode=periode,
        type_evaluation=type_evaluation,
        search=search,
        eleve_classe_map=eleve_classe_map,
        annee_active=annee_consultee,
        annee_consultee=annee_consultee,
        message_annee=message_annee,
        notes_modifiables=peut_modifier,
    )


@main.route('/notes/export_excel')
@login_required
@role_required('admin')
def export_notes_excel():
    """Export Excel de toutes les notes avec jointures élèves/cours pour l'année consultée."""
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)

    notes = get_notes_annee(
        ecole_id=ecole_id,
        annee=annee_consultee,
        user=current_user,
    )

    data = {
        'Date': [n.date_evaluation.strftime('%d/%m/%Y') if n.date_evaluation else '' for n in notes],
        'Élève': [f"{n.eleve.prenom} {n.eleve.nom}" if n.eleve else '' for n in notes],
        'Classe': [
            n.inscription.classe.nom if (n.inscription and n.inscription.classe)
            else (n.cours.classe.nom if (n.cours and n.cours.classe) else 'Sans classe')
            for n in notes
        ],
        'Cours': [n.cours.nom if n.cours else '' for n in notes],
        'Note': [n.valeur for n in notes],
        'Coefficient': [n.coefficient for n in notes],
        'Type': [n.type_evaluation for n in notes],
        'Période': [n.periode for n in notes],
    }

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Notes', index=False)
    output.seek(0)

    suffix = f"_{annee_consultee.nom.replace('/', '-')}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"liste_notes{suffix}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@main.route('/notes/saisie_classe', methods=['GET', 'POST'], endpoint='saisie_notes_classe')
@login_required
@role_required('professeur')
def saisie_notes_classe():
    """Saisie rapide des notes par classe entière (Phase 5C)."""
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    message_annee = statut_annee_notes(annee_consultee)
    peut_modifier = notes_modifiables(annee_consultee, current_user)

    if request.method == 'POST':
        if not peut_modifier:
            if annee_consultee and annee_consultee.statut == 'archivee':
                flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
            elif annee_consultee and annee_consultee.statut == 'planifiee':
                flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
            else:
                flash("Action non autorisée pour cette année scolaire.", "danger")
            return redirect(url_for('main.notes'))

        classe_id = request.form.get('classe_id', type=int)
        cours_id = request.form.get('cours_id', type=int)
        periode = request.form.get('periode', 'Semestre 1')
        type_evaluation = request.form.get('type_evaluation', 'Devoir')
        coefficient = request.form.get('coefficient', 1.0, type=float)

        notes_dict = {}
        for key, val in request.form.items():
            if key.startswith('note_'):
                eleve_id_raw = key[5:]
                if eleve_id_raw.isdigit():
                    notes_dict[int(eleve_id_raw)] = val

        nb_notes, err = service_saisir_notes_classe(
            ecole_id=ecole_id,
            annee=annee_consultee,
            user=current_user,
            classe_id=classe_id,
            cours_id=cours_id,
            notes_dict=notes_dict,
            periode=periode,
            type_evaluation=type_evaluation,
            coefficient=coefficient,
            date_evaluation=datetime.utcnow(),
        )

        if err:
            flash(err, "danger")
            return redirect(url_for('main.saisie_notes_classe', classe_id=classe_id, cours_id=cours_id))
        else:
            flash(f"{nb_notes} note(s) enregistrée(s) avec succès pour la classe.", "success")
            return redirect(url_for('main.notes'))

    # GET: Préparation de la grille
    classes = get_classes_notes(ecole_id, annee_consultee, user=current_user)

    selected_classe_id = request.args.get('classe_id', type=int)
    valides_classe_ids = [c.id for c in classes]
    if selected_classe_id not in valides_classe_ids:
        selected_classe_id = valides_classe_ids[0] if valides_classe_ids else None

    cours_disponibles = []
    if selected_classe_id:
        cours_disponibles = get_cours_annee(ecole_id, annee_consultee, user=current_user, classe_id=selected_classe_id)

    selected_cours_id = request.args.get('cours_id', type=int)
    valides_cours_ids = [c.id for c in cours_disponibles]
    if selected_cours_id not in valides_cours_ids:
        selected_cours_id = valides_cours_ids[0] if valides_cours_ids else None

    inscriptions = []
    if selected_classe_id:
        inscriptions = get_inscriptions_notes(ecole_id, annee_consultee, user=current_user, classe_id=selected_classe_id)

    tous_les_cours = get_cours_annee(ecole_id, annee_consultee, user=current_user)

    return render_template(
        'saisie_notes_classe.html',
        classes=classes,
        cours_disponibles=cours_disponibles,
        tous_les_cours=tous_les_cours,
        inscriptions=inscriptions,
        selected_classe_id=selected_classe_id,
        selected_cours_id=selected_cours_id,
        annee_consultee=annee_consultee,
        notes_modifiables=peut_modifier,
        message_annee=message_annee,
    )


@main.route('/note/<int:note_id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'professeur')
def modifier_note(note_id):
    note = Note.query.get_or_404(note_id)
    if not can_manage_note(note):
        abort(403)

    ecole_id = current_user.ecole_id
    if note.ecole_id != ecole_id:
        abort(403)

    # Récupérer l'année scolaire de la note
    annee_note = note.annee
    if not annee_note and note.inscription:
        annee_note = note.inscription.annee_scolaire
    if not annee_note and note.annee_id:
        annee_note = AnneeScolaire.query.get(note.annee_id)

    # Contrôle de cycle de vie annuel
    if not annee_note or annee_note.statut != 'active':
        if annee_note and annee_note.statut == 'archivee':
            flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
        elif annee_note and annee_note.statut == 'planifiee':
            flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
        else:
            flash("Vous ne pouvez modifier une note que pour une année scolaire active.", "warning")
        return redirect(url_for('main.notes'))

    # Permissions professeurs
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if not professeur:
            professeur = Professeur.query.filter_by(
                utilisateur_id=current_user.id,
                ecole_id=ecole_id
            ).first()
        if not professeur or (note.cours and note.cours.professeur_id != professeur.id):
            abort(403)

    form = NoteForm(obj=note)

    # Choix limités à l'année de la note
    eleve_choices = get_eleves_choices_notes(ecole_id, annee_note, user=current_user)
    form.eleve_id.choices = eleve_choices or [(note.eleve_id, f"{note.eleve.prenom} {note.eleve.nom}" if note.eleve else "Élève")]

    cours_choices = get_cours_choices_notes(ecole_id, annee_note, user=current_user)
    form.cours_id.choices = cours_choices or [(note.cours_id, note.cours.nom if note.cours else "Cours")]

    if hasattr(form, 'annee_id'):
        form.annee_id.choices = [(annee_note.id, annee_note.nom)]
        form.annee_id.data = annee_note.id

    if form.validate_on_submit():
        _, err = service_modifier_note(
            ecole_id=ecole_id,
            annee=annee_note,
            user=current_user,
            note_id=note.id,
            valeur=form.valeur.data,
            coefficient=form.coefficient.data,
            type_evaluation=form.type_evaluation.data,
            periode=form.periode.data,
            eleve_id=form.eleve_id.data,
            cours_id=form.cours_id.data,
        )

        if err:
            flash(err, "danger")
        else:
            flash("Note modifiée avec succès", "success")
            return redirect(url_for('main.notes'))

    return render_template(
        'modifier_note.html',
        form=form,
        note=note,
        annee_note=annee_note,
    )


@main.route('/notes/supprimer/<int:note_id>', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
def supprimer_note(note_id):
    note = Note.query.get_or_404(note_id)
    if not can_manage_note(note):
        abort(403)

    ecole_id = current_user.ecole_id
    if note.ecole_id != ecole_id:
        abort(403)

    annee_note = note.annee
    if not annee_note and note.inscription:
        annee_note = note.inscription.annee_scolaire
    if not annee_note and note.annee_id:
        annee_note = AnneeScolaire.query.get(note.annee_id)

    succes, err = service_supprimer_note(
        ecole_id=ecole_id,
        annee=annee_note,
        user=current_user,
        note_id=note.id,
    )

    if not succes:
        flash(err or "Erreur lors de la suppression de la note.", "danger")
    else:
        flash("Note supprimée avec succès.", "success")

    return redirect(url_for('main.notes'))
