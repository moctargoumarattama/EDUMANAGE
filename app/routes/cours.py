from app.utils_classes import classes_triees_pedagogique, ordre_pedagogique_classe
from app.models import NiveauScolaire
from . import main
from .common import (
    BytesIO,
    Classe,
    Cours,
    CoursForm,
    DeleteForm,
    Eleve,
    HistoriqueImport,
    IntegrityError,
    Note,
    Professeur,
    Utilisateur,
    abort,
    can_manage_cours,
    current_app,
    current_user,
    datetime,
    db,
    ecole_required,
    filtre_par_ecole,
    flash,
    get_ecole_courante,
    io,
    joinedload,
    json,
    jsonify,
    login_required,
    os,
    redirect,
    render_template,
    request,
    role_required,
    send_file,
    url_for,
)
from unidecode import unidecode
import pandas as pd
from app.services import check_ecole_access
from app.services.annees_scolaires import get_annee_consultee
from app.services.classes_annuelles import classe_est_ouverte
from app.services.cours_annuels import valider_classe_pour_nouveau_cours
from app.services.cours_uniqueness import find_duplicate_cours, normalize_cours_nom
from app.utils import get_annee_active


def _professeurs_affectables(ecole_id):
    return (
        Professeur.query
        .filter_by(ecole_id=ecole_id)
        .order_by(Professeur.nom.asc(), Professeur.prenom.asc(), Professeur.id.asc())
        .all()
    )


def _professeur_choices(professeurs):
    return [(0, "Non affecte")] + [
        (prof.id, f"{prof.prenom} {prof.nom}") for prof in professeurs
    ]


def _cours_annee_query(ecole_id, annee_id):
    query = (
        Cours.query
        .join(Classe, Classe.id == Cours.classe_id)
        .options(joinedload(Cours.classe), joinedload(Cours.professeur), joinedload(Cours.notes))
        .filter(Cours.ecole_id == ecole_id, Classe.ecole_id == ecole_id)
    )
    if annee_id:
        query = query.filter(Classe.annee_scolaire_id == annee_id)
    else:
        query = query.filter(False)
    return query


def _valider_affectation_professeur(ecole_id, cours_id, professeur_id):
    cours = (
        Cours.query
        .join(Classe, Classe.id == Cours.classe_id)
        .options(joinedload(Cours.classe).joinedload(Classe.annee_scolaire))
        .filter(Cours.id == cours_id, Cours.ecole_id == ecole_id, Classe.ecole_id == ecole_id)
        .first()
    )
    if not cours:
        return None, None, "Cours introuvable pour cet etablissement."
    if not cours.classe:
        return None, None, "Ce cours n'est rattache a aucune classe."
    if cours.classe.annee_scolaire and cours.classe.annee_scolaire.statut == "archivee":
        return None, None, "Impossible de modifier une affectation dans une annee archivee."
    if not classe_est_ouverte(cours.classe):
        return None, None, "Impossible de modifier une affectation dans une classe fermee."
    if not professeur_id:
        return cours, None, None

    professeur = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
    if not professeur:
        return None, None, "Professeur invalide pour cet etablissement."
    return cours, professeur, None


@main.route('/cours')
@login_required
@role_required('admin', 'super_admin', 'professeur')
def cours():
    ecole_courante = get_ecole_courante()
    delete_form = DeleteForm()
    annee_consultee = get_annee_consultee(ecole_courante.id)

    def cours_to_dict(cours_item):
        return {
            'id': cours_item.id,
            'nom': cours_item.nom,
            'description': cours_item.description or "",
            'coefficient': cours_item.coefficient,
            'classe': {
                'id': cours_item.classe.id,
                'nom': cours_item.classe.nom,
                'niveau': cours_item.classe.niveau,
                'niveau_ordre': (cours_item.classe.niveau_scolaire.ordre if cours_item.classe.niveau_scolaire else 999),
                'statut': cours_item.classe.statut,
                'annee_scolaire_id': cours_item.classe.annee_scolaire_id,
            } if cours_item.classe else None,
            'professeur': {
                'id': cours_item.professeur.id,
                'prenom': cours_item.professeur.prenom,
                'nom': cours_item.professeur.nom,
            } if cours_item.professeur else None,
            'notes_count': len(cours_item.notes) if hasattr(cours_item, 'notes') else 0,
            'ecole_id': cours_item.ecole_id,
        }

    search = (request.args.get('search') or request.args.get('q') or '').strip()
    classe_id = request.args.get('classe_id', type=int) or request.args.get('classe', type=int)
    niveau = (request.args.get('niveau') or '').strip()
    professeur_id = request.args.get('professeur_id', type=int) or request.args.get('professeur', type=int)
    affectation = (request.args.get('affectation') or '').strip().lower()

    if current_user.role in ('admin', 'super_admin'):
        form = CoursForm()
        professeurs = _professeurs_affectables(ecole_courante.id)
        classes = classes_triees_pedagogique(
            Classe.query
            .filter_by(ecole_id=ecole_courante.id, statut="ouverte")
            .filter(~Classe.annee_scolaire.has(statut="archivee"))
            .filter(Classe.annee_scolaire_id == annee_consultee.id if annee_consultee else False)
        ).all()
        base_query = _cours_annee_query(ecole_courante.id, annee_consultee.id if annee_consultee else None)

        if search:
            pat = f"%{search}%"
            base_query = base_query.outerjoin(Professeur, Professeur.id == Cours.professeur_id).filter(
                db.or_(
                    Cours.nom.ilike(pat),
                    Classe.nom.ilike(pat),
                    Classe.niveau.ilike(pat),
                    Professeur.nom.ilike(pat),
                    Professeur.prenom.ilike(pat)
                )
            )
        if classe_id:
            c_val = Classe.query.filter_by(id=classe_id, ecole_id=ecole_courante.id).first()
            if not c_val:
                base_query = base_query.filter(db.false())
            else:
                base_query = base_query.filter(Cours.classe_id == classe_id)
        if niveau:
            base_query = base_query.filter(Classe.niveau == niveau)
        if professeur_id:
            base_query = base_query.filter(Cours.professeur_id == professeur_id)
        if affectation == 'avec_prof':
            base_query = base_query.filter(Cours.professeur_id.isnot(None))
        elif affectation == 'sans_prof':
            base_query = base_query.filter(Cours.professeur_id.is_(None))

        tous_cours = base_query.outerjoin(NiveauScolaire, Classe.niveau_id == NiveauScolaire.id).order_by(*ordre_pedagogique_classe(), Cours.nom.asc()).all()
        form.professeur_id.choices = _professeur_choices(professeurs)
        form.classe_id.choices = [
            (classe.id, f"{classe.nom} ({classe.niveau})")
            for classe in classes
        ]
        professeurs_actifs = len({c.professeur_id for c in tous_cours if c.professeur_id})
        notes_total = sum(len(c.notes) for c in tous_cours if hasattr(c, 'notes'))
        cours_total = len(tous_cours)
        cours_json = [cours_to_dict(c) for c in tous_cours]
        cours_source = tous_cours
    else:
        form = None
        professeurs = []
        professeur = Professeur.query.filter_by(
            utilisateur_id=current_user.id,
            ecole_id=ecole_courante.id,
        ).first()
        if not professeur:
            flash("Profil enseignant introuvable pour cette ecole.", "danger")
            return redirect(url_for('main.index'))
        prof_query = (
            _cours_annee_query(ecole_courante.id, annee_consultee.id if annee_consultee else None)
            .filter(Cours.professeur_id == professeur.id)
        )
        if search:
            pat = f"%{search}%"
            prof_query = prof_query.filter(
                db.or_(
                    Cours.nom.ilike(pat),
                    Classe.nom.ilike(pat),
                    Classe.niveau.ilike(pat)
                )
            )
        if classe_id:
            prof_query = prof_query.filter(Cours.classe_id == classe_id)
        if niveau:
            prof_query = prof_query.filter(Classe.niveau == niveau)

        mes_cours = prof_query.outerjoin(NiveauScolaire, Classe.niveau_id == NiveauScolaire.id).order_by(*ordre_pedagogique_classe(), Cours.nom.asc()).all()
        notes_total = sum(len(c.notes) for c in mes_cours if hasattr(c, 'notes'))
        cours_total = len(mes_cours)
        professeurs_actifs = 1
        cours_json = [cours_to_dict(c) for c in mes_cours]
        cours_source = mes_cours

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': cours_total,
            'cours': cours_json
        })

    return render_template(
        'cours.html',
        cours=cours_json,
        form=form,
        delete_form=delete_form,
        professeurs_count=professeurs_actifs,
        notes_count=notes_total,
        cours_count=cours_total,
        ecole_nom=ecole_courante.nom if ecole_courante else "Systeme",
        annee_consultee=annee_consultee,
        professeurs=professeurs,
        search=search,
        classe_id=classe_id,
        niveau=niveau,
        professeur_id=professeur_id,
        affectation=affectation,
        cours_sans_professeur=sum(1 for c in cours_source if not c.professeur_id),
        affectations_modifiables=bool(
            annee_consultee
            and annee_consultee.statut != "archivee"
            and current_user.role in ('admin', 'super_admin')
        ),
    )


@main.route('/cours/<int:cours_id>/professeur', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def affecter_professeur_cours(cours_id):
    ecole_courante = get_ecole_courante()
    payload = request.get_json(silent=True) if request.is_json else None
    raw_professeur_id = payload.get("professeur_id") if isinstance(payload, dict) else request.form.get("professeur_id")
    try:
        professeur_id = int(raw_professeur_id) if raw_professeur_id not in (None, "") else None
    except (TypeError, ValueError):
        professeur_id = None
    professeur_id = professeur_id or None
    cours_obj, professeur, error = _valider_affectation_professeur(ecole_courante.id, cours_id, professeur_id)
    if error:
        if request.is_json:
            return jsonify({"success": False, "message": error}), 400
        flash(error, "danger")
        return redirect(request.referrer or url_for('main.cours'))

    cours_obj.professeur_id = professeur.id if professeur else None
    db.session.commit()

    if request.is_json:
        return jsonify({"success": True, "professeur_id": cours_obj.professeur_id})
    flash("Affectation professeur enregistree.", "success")
    return redirect(request.referrer or url_for('main.cours'))


@main.route('/ajouter_cours', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def ajouter_cours():
    ecole_courante = get_ecole_courante()
    annee_consultee = get_annee_consultee(ecole_courante.id)
    form = CoursForm()

    # Choix restreints ? l'école courante
    form.professeur_id.choices = _professeur_choices(_professeurs_affectables(ecole_courante.id))
    form.classe_id.choices = [
        (c.id, f"{c.nom} ({c.niveau})") for c in classes_triees_pedagogique(
            Classe.query
            .filter_by(ecole_id=ecole_courante.id, statut="ouverte")
            .filter(~Classe.annee_scolaire.has(statut="archivee"))
            .filter(Classe.annee_scolaire_id == annee_consultee.id if annee_consultee else False)
        ).all()
    ]

    if form.validate_on_submit():
        try:
            # Vérification stricte dans l'école courante
            prof = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_courante.id).first() if form.professeur_id.data else None
            classe, classe_error = valider_classe_pour_nouveau_cours(ecole_courante.id, form.classe_id.data)

            if classe_error:
                flash(classe_error, "danger")
                return redirect(url_for('main.cours'))
            if not annee_consultee or classe.annee_scolaire_id != annee_consultee.id:
                flash("Classe invalide pour l'annee consultee.", "danger")
                return redirect(url_for('main.cours'))
            classe = (
                Classe.query
                .filter_by(id=classe.id, ecole_id=ecole_courante.id)
                .with_for_update()
                .first()
            )

            nom_cours = normalize_cours_nom(form.nom.data)
            doublon = find_duplicate_cours(ecole_courante.id, classe.id, nom_cours)
            if doublon:
                flash("Un cours avec ce nom existe d?j? pour cette classe.", "danger")
                return redirect(url_for('main.cours'))

            nouveau_cours = Cours(
                nom=nom_cours,
                description=form.description.data,
                coefficient=form.coefficient.data,
                professeur_id=prof.id if prof else None,
                classe_id=classe.id,
                ecole_id=ecole_courante.id
            )

            db.session.add(nouveau_cours)
            db.session.commit()

            current_app.log_correction(
                action="ajout",
                description=f"Cours ajouté : {nouveau_cours.nom}",
                ecole_id=ecole_courante.id,
                cible_type="cours",
                cible_id=nouveau_cours.id,
                ancienne_valeur=None,
                nouvelle_valeur=json.dumps({
                    "nom": nouveau_cours.nom,
                    "coefficient": nouveau_cours.coefficient,
                    "professeur_id": nouveau_cours.professeur_id,
                    "classe_id": nouveau_cours.classe_id
                }),
                niveau="info"
            )

            flash('Cours ajouté avec succès', 'success')

        except IntegrityError as e:
            db.session.rollback()
            flash("Erreur d’intégrité (doublon possible).", "danger")
            current_app.logger.error(f"IntegrityError cours: {e}")

        except Exception as e:
            db.session.rollback()
            flash("Erreur inattendue lors de l'ajout du cours.", "danger")
            current_app.logger.error(f"Erreur ajout cours: {e}")

    else:
        flash("Le formulaire contient des erreurs.", "warning")

    return redirect(url_for('main.cours'))

@main.route('/cours/<int:id>')
@login_required
@role_required('admin', 'professeur')
def cours_details(id):
    cours = Cours.query.options(
        joinedload(Cours.professeur),
        joinedload(Cours.notes).joinedload(Note.eleve)
    ).get_or_404(id)

    if not can_manage_cours(cours):
        abort(403)

    notes = sorted(cours.notes, key=lambda n: n.date_evaluation, reverse=True)
    total_pondere = sum(n.valeur * n.coefficient for n in notes)
    total_coefficients = sum(n.coefficient for n in notes)
    moyenne_cours = round(total_pondere / total_coefficients, 2) if total_coefficients else 0
    eleves_avec_notes = len(set(n.eleve_id for n in notes))

    return render_template('cours_details.html',
                           cours=cours,
                           notes=notes,
                           moyenne_cours=moyenne_cours,
                           eleves_avec_notes=eleves_avec_notes)

@main.route('/cours/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_cours(id):
    ecole_courante = get_ecole_courante()
    cours = Cours.query.filter_by(id=id, ecole_id=ecole_courante.id).first_or_404()
    form = CoursForm(obj=cours)
    professeurs = Professeur.query.filter_by(ecole_id=ecole_courante.id).order_by(Professeur.nom).all()
    classes = classes_triees_pedagogique(Classe.query.filter_by(ecole_id=ecole_courante.id)).all()
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in professeurs]
    form.classe_id.choices = [(c.id, f"{c.nom} ({c.niveau})") for c in classes]

    if request.method == 'GET':
        form.professeur_id.data = cours.professeur_id
        form.classe_id.data = cours.classe_id

    if form.validate_on_submit():
        professeur = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_courante.id).first()
        if form.classe_id.data == cours.classe_id:
            classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=ecole_courante.id).first()
            classe_error = None
        else:
            classe, classe_error = valider_classe_pour_nouveau_cours(ecole_courante.id, form.classe_id.data)
        if not professeur or not classe:
            flash("Le professeur ou la classe n'appartient pas ? votre école.", "danger")
            return redirect(url_for('main.modifier_cours', id=cours.id))
        if classe_error:
            flash(classe_error, "danger")
            return redirect(url_for('main.modifier_cours', id=cours.id))

        nom_cours = normalize_cours_nom(form.nom.data)
        doublon = find_duplicate_cours(ecole_courante.id, classe.id, nom_cours, exclude_id=cours.id)
        if doublon:
            flash("Un cours avec ce nom existe d?j? pour cette classe.", "danger")
            return redirect(url_for('main.modifier_cours', id=cours.id))

        cours.nom = nom_cours
        cours.description = form.description.data
        cours.coefficient = form.coefficient.data
        cours.professeur_id = professeur.id
        cours.classe_id = classe.id
        db.session.commit()
        flash("Cours modifié avec succès.", "success")
        return redirect(url_for('main.cours_details', id=cours.id))

    return render_template('modifier_cours.html', form=form, cours=cours)

@main.route('/cours/<int:id>/export_notes')
@login_required
@role_required('admin', 'professeur')
def export_notes(id):
    """Export des notes d'un cours spécifique en Excel"""
    cours = Cours.query.options(joinedload(Cours.notes).joinedload(Note.eleve)).filter_by(
        id=id,
        ecole_id=current_user.ecole_id
    ).first_or_404()

    if not can_manage_cours(cours):
        abort(403)

    # Préparation des données
    data = [{
        "Élève ID": note.eleve.id,
        "Nom": note.eleve.nom,
        "Prénom": note.eleve.prenom,
        "Note": note.valeur,
        "Coefficient": note.coefficient,
        "Date": note.date_evaluation.strftime("%d/%m/%Y") if note.date_evaluation else ""
    } for note in cours.notes]

    # Création du fichier Excel
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=cours.nom[:30])

    output.seek(0)
    return send_file(
        output,
        download_name=f"Notes_{cours.nom}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@main.route('/cours/<int:id>/import_notes_excel', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
def import_notes_excel(id):
    """Import de notes depuis Excel/CSV avec historique minimal."""
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    if not can_manage_cours(cours):
        abort(403)

    if cours.classe_id:
        annee = cours.classe.annee_scolaire if cours.classe else None
        if not annee or annee.ecole_id != cours.ecole_id:
            flash("La classe de ce cours n'est associée à aucune année scolaire valide.", "danger")
            return redirect(url_for('main.cours_details', id=id))
    else:
        annee = get_annee_active(cours.ecole_id)
        if not annee:
            flash("Aucune année scolaire active n'est disponible pour ce cours.", "danger")
            return redirect(url_for('main.cours_details', id=id))

    file = request.files.get("file")
    if not file or file.filename == '':
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for('main.cours_details', id=id))

    if not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        flash("Format de fichier non pris en charge.", "danger")
        return redirect(url_for('main.cours_details', id=id))

    try:
        # Lecture du fichier (Excel ou CSV)
        if file.filename.lower().endswith(".csv"):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Normalisation des noms de colonnes
        df.columns = [unidecode(c).lower().strip() for c in df.columns]

        # Colonnes acceptées
        required_cols = [
            ["nom", "prenom", "classe", "note"],
            ["eleve", "classe", "note"],
            ["eleve id", "note"]
        ]
        if not any(all(col in df.columns for col in cols) for cols in required_cols):
            flash("Format de fichier incorrect. Vérifiez les colonnes.", "danger")
            return redirect(url_for('main.cours_details', id=id))

        notes_importees, erreurs = 0, []

        # Préchargement des élèves de la même école
        eleves_dict = {e.id: e for e in Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()}

        # Parcours du fichier
        for index, row in df.iterrows():
            try:
                eleve = None
                nom, prenom, classe = None, None, None

                # Recherche par ID
                if "eleve id" in df.columns and pd.notna(row["eleve id"]):
                    eleve = eleves_dict.get(int(row["eleve id"]))
                    if eleve:
                        nom, prenom = eleve.nom, eleve.prenom

                # Recherche par nom/prénom
                elif "nom" in df.columns and "prenom" in df.columns:
                    nom = str(row["nom"]).strip()
                    prenom = str(row["prenom"]).strip()
                    classe = str(row["classe"]).strip() if pd.notna(row.get("classe")) else None
                    eleve = next(
                        (e for e in eleves_dict.values()
                         if e.nom.lower() == nom.lower()
                         and e.prenom.lower() == prenom.lower()
                         and (not classe or e.classe.lower() == classe.lower())),
                        None
                    )

                # Recherche par colonne unique "élève"
                else:
                    nom_complet = str(row["eleve"]).strip()
                    parties = nom_complet.split()
                    if len(parties) >= 2:
                        prenom, nom = " ".join(parties[:-1]), parties[-1]
                        classe = str(row["classe"]).strip() if pd.notna(row.get("classe")) else None
                        eleve = next(
                            (e for e in eleves_dict.values()
                             if e.nom.lower() == nom.lower()
                             and e.prenom.lower() == prenom.lower()
                             and (not classe or e.classe.lower() == classe.lower())),
                            None
                        )

                if not eleve:
                    erreurs.append(f"Ligne {index+2}: Élève non trouvé ({prenom or ''} {nom or ''})")
                    continue

                if eleve.ecole_id != cours.ecole_id:
                    erreurs.append(f"Ligne {index+2}: Élève associé ? une autre école")
                    continue

                # Vérification de la note
                try:
                    note_valeur = float(row["note"])
                    if not (0 <= note_valeur <= 20):
                        erreurs.append(f"Ligne {index+2}: Note invalide ({note_valeur})")
                        continue
                except (TypeError, ValueError):
                    erreurs.append(f"Ligne {index+2}: Format de note invalide ({row['note']})")
                    continue

                # Ajout / mise ? jour
                note = Note.query.filter_by(cours_id=id, eleve_id=eleve.id, ecole_id=cours.ecole_id).first()
                if note:
                    if note.annee_id and note.annee_id != annee.id:
                        erreurs.append(
                            f"Ligne {index+2}: Note existante associée ? une autre année scolaire"
                        )
                        continue
                    note.valeur = note_valeur
                    if note.annee_id is None:
                        note.annee_id = annee.id
                else:
                    db.session.add(Note(
                        cours_id=id,
                        eleve_id=eleve.id,
                        valeur=note_valeur,
                        ecole_id=cours.ecole_id,
                        annee_id=annee.id
                    ))

                notes_importees += 1

            except Exception as e:
                erreurs.append(f"Ligne {index+2}: {str(e)}")
                continue

        historique = HistoriqueImport(
            fichier=file.filename,
            utilisateur_id=current_user.id,
        )
        db.session.add(historique)
        db.session.commit()

        # Feedback utilisateur
        if notes_importees:
            flash(f"{notes_importees} notes importées avec succès.", "success")
        if erreurs:
            flash(f"{len(erreurs)} lignes ignorées car invalides.", "warning")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur lors de l'import des notes du cours %s", id)
        flash("Erreur lors de l'import des notes.", "danger")

    return redirect(url_for('main.cours_details', id=id))

@main.route('/imports/telecharger/<filename>')
@login_required
def telecharger_import(filename):
    """Télécharger le fichier d'erreurs d'import"""

    from werkzeug.utils import secure_filename
    import os
    from flask import send_from_directory, abort, current_app

    # Nom de fichier sécurisé
    safe_filename = secure_filename(filename)

    # Vérification stricte du nom pour éviter les fichiers non autorisés
    if not safe_filename.startswith('errors_import_') or not safe_filename.endswith('.csv'):
        abort(404, "Fichier non autorisé")

    imports_dir = os.path.join(current_app.root_path, "static", "imports")
    file_path = os.path.join(imports_dir, safe_filename)

    # Vérification que le fichier existe bien
    if not os.path.isfile(file_path):
        abort(404, "Fichier non trouvé")

    return send_from_directory(imports_dir, safe_filename, as_attachment=True)

@main.route('/cours/<int:id>/modele_import_notes')
@login_required
@role_required('admin', 'professeur')
def modele_import_notes(id):
    """Téléchargement d'un modèle d'importation de notes (Excel ou CSV)"""

    format_fichier = request.args.get('format', 'excel').lower()
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    if not can_manage_cours(cours):
        abort(403)

    colonnes = ['Nom', 'Prénom', 'Classe', 'Note', 'Coefficient', 'Type évaluation']
    df = pd.DataFrame(columns=colonnes)

    # --- Génération CSV ---
    if format_fichier == 'csv':
        output = BytesIO()
        df.to_csv(output, index=False, sep=',', encoding='utf-8-sig')
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=f"modele_import_notes_cours_{cours.nom}.csv",
            mimetype='text/csv'
        )

    # --- Génération Excel ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Deux feuilles identiques pour donner un choix ? l’utilisateur
        df.to_excel(writer, sheet_name='Format Standard', index=False)
        df.to_excel(writer, sheet_name='Format Alternatif', index=False)

        # Mise en forme visuelle
        workbook = writer.book
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        for sheet_name in ['Format Standard', 'Format Alternatif']:
            worksheet = writer.sheets[sheet_name]
            for col_num, value in enumerate(df.columns):
                worksheet.write(0, col_num, value, header_format)
            for i in range(len(df.columns)):
                worksheet.set_column(i, i, 20)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"modele_import_notes_cours_{cours.nom}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@main.route('/imports/historique')
@login_required
@role_required('admin', 'professeur')
def imports_historique():
    """Affichage de l'historique des imports filtré par école"""
    historiques = (
        HistoriqueImport.query
        .join(HistoriqueImport.utilisateur)
        .filter(Utilisateur.ecole_id == current_user.ecole_id)
        .order_by(HistoriqueImport.date_import.desc())
        .all()
    )
    return render_template("imports_historique.html", historiques=historiques)

@main.route('/cours/<int:id>/supprimer', methods=['POST'])
@main.route('/matiere/<int:id>/supprimer', methods=['POST'])
@main.route('/matieres/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_cours(id):
    from app.models import Absence, EmploiTemps
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.is_json
        or request.accept_mimetypes.best == 'application/json'
    )
    cours = Cours.query.get(id)
    if not cours:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Matière/cours introuvable.'}), 404
        abort(404)

    # 🛡️ Sécurité multi-écoles : contrôle strict cross-tenant
    if cours.ecole_id != current_user.ecole_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Action non autorisée.'}), 403
        abort(403)

    try:
        # Vérifier si la matière/cours est rattachée à des évaluations (notes), absences ou emplois du temps
        has_notes = Note.query.filter_by(cours_id=cours.id).first() is not None
        has_absences = Absence.query.filter_by(cours_id=cours.id).first() is not None
        has_emplois = EmploiTemps.query.filter_by(cours_id=cours.id).first() is not None

        if has_notes or has_absences or has_emplois:
            msg = "Impossible de supprimer cette matière car elle contient des évaluations, absences ou séances d'emploi du temps associées."
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, "warning")
            return redirect(url_for('main.cours'))

        deleted_id = cours.id
        cours_nom = cours.nom
        cours_ecole_id = cours.ecole_id
        ancienne_valeur = f"Cours: {cours.nom} (Prof: {cours.professeur_id}, Classe: {cours.classe_id})"

        db.session.delete(cours)
        db.session.commit()

        # Journalisation
        if hasattr(current_app, "log_correction"):
            current_app.log_correction(
                action="suppression_cours",
                description=f"Cours supprimé : {cours_nom}",
                ecole_id=cours_ecole_id,
                cible_type="cours",
                cible_id=id,
                ancienne_valeur=ancienne_valeur,
                nouvelle_valeur=None,
                niveau="info"
            )

        if is_ajax:
            return jsonify({'success': True, 'message': 'Matière/cours supprimé avec succès.', 'deleted_id': deleted_id})
        flash("Matière supprimée avec succès.", "success")
        return redirect(url_for('main.cours'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression cours {id}: {e}")
        message = "Suppression impossible. Veuillez réessayer."
        if is_ajax:
            return jsonify({'success': False, 'message': message}), 500
        flash(message, "danger")
        return redirect(url_for('main.cours'))


@main.route('/classe/<int:classe_id>/charger-matieres-standard', methods=['POST'])
@login_required
@role_required('admin')
@ecole_required
def charger_matieres_standard(classe_id):
    """Charge automatiquement les matières et coefficients standard pour une classe."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json

    classe = Classe.query.get(classe_id)
    if not classe:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Classe introuvable.'}), 404
        flash("Classe introuvable.", "danger")
        return redirect(url_for('main.liste_classes'))

    if classe.ecole_id != current_user.ecole_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Accès non autorisé.'}), 403
        abort(403)

    from app.services.pedagogie_standard import injecter_matieres_standard
    cours_crees, err = injecter_matieres_standard(
        ecole_id=current_user.ecole_id,
        annee_id=classe.annee_scolaire_id,
        classe_id=classe.id
    )

    if err:
        if is_ajax:
            return jsonify({'success': False, 'message': err}), 400
        flash(err, "warning")
        return redirect(url_for('main.detail_classe', classe_id=classe.id))

    count = len(cours_crees)
    if count > 0:
        msg = f"{count} matière(s) standard ajoutée(s) à la classe {classe.nom}."
        if is_ajax:
            return jsonify({
                'success': True,
                'message': msg,
                'count': count,
                'cours': [{'id': c.id, 'nom': c.nom, 'coefficient': c.coefficient} for c in cours_crees]
            })
        flash(msg, "success")
    else:
        msg = f"Toutes les matières standards sont déjà configurées pour la classe {classe.nom}."
        if is_ajax:
            return jsonify({'success': True, 'message': msg, 'count': 0, 'cours': []})
        flash(msg, "info")

    next_url = request.form.get('next') or url_for('main.detail_classe', classe_id=classe.id)
    return redirect(next_url)

