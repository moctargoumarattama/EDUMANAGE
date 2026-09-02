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
    current_app,
    current_user,
    datetime,
    db,
    filtre_par_ecole,
    flash,
    get_ecole_courante,
    io,
    joinedload,
    json,
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
from app.utils import get_annee_active


@main.route('/cours')
@login_required
@role_required('admin', 'enseignant', 'professeur')
def cours():
    """
    Page de gestion des cours filtrée par école
    Affichage différencié selon le rôle utilisateur
    """
    
    # === INITIALISATION DES DONNÉES DE BASE ===
    ecole_courante = get_ecole_courante()
    delete_form = DeleteForm()
    is_super_admin = getattr(current_user, 'is_super_admin', False)
    
    # === FONCTION UTILITAIRE POUR LA SÉRIALISATION JSON ===
    def cours_to_dict(cours_item):
        """Transforme un objet Cours en dictionnaire pour JSON"""
        return {
            'id': cours_item.id,
            'nom': cours_item.nom,
            'description': cours_item.description or "",
            'coefficient': cours_item.coefficient,
            'classe': {
                'id': cours_item.classe.id,
                'nom': cours_item.classe.nom,
                'niveau': cours_item.classe.niveau
            } if cours_item.classe else None,
            'professeur': {
                'id': cours_item.professeur.id,
                'prenom': cours_item.professeur.prenom,
                'nom': cours_item.professeur.nom
            } if cours_item.professeur else None,
            'notes_count': len(cours_item.notes) if hasattr(cours_item, 'notes') else 0,
            'ecole_id': cours_item.ecole_id
        }

    # === LOGIQUE SPÉCIFIQUE PAR RÔLE ===
    
    if current_user.role == 'admin':
        # === ADMINISTRATEUR ===
        form = CoursForm()
        
        # Récupération des données de l'école
        professeurs = Professeur.query.filter_by(
            ecole_id=ecole_courante.id
        ).order_by(Professeur.nom, Professeur.prenom).all()
        
        classes = Classe.query.filter_by(
            ecole_id=ecole_courante.id
        ).order_by(Classe.niveau, Classe.nom).all()
        
        # Récupération des cours avec filtre super admin
        if is_super_admin:
            tous_cours = Cours.query.order_by(Cours.nom).all()
        else:
            tous_cours = Cours.query.filter_by(
                ecole_id=ecole_courante.id
            ).order_by(Cours.nom).all()
        
        # Peuplement des choix des formulaires
        form.professeur_id.choices = [
            (prof.id, f"{prof.prenom} {prof.nom}") 
            for prof in professeurs
        ]
        form.classe_id.choices = [
            (classe.id, f"{classe.nom} ({classe.niveau})") 
            for classe in classes
        ]
        
        # Calcul des statistiques
        professeurs_actifs = len(set([
            c.professeur_id for c in tous_cours 
            if c.professeur_id
        ]))
        notes_total = sum([
            len(c.notes) for c in tous_cours 
            if hasattr(c, 'notes')
        ])
        cours_total = len(tous_cours)
        
        # Sérialisation JSON
        cours_json = [cours_to_dict(c) for c in tous_cours]
        
    else:
        # === ENSEIGNANT / PROFESSEUR ===
        form = None
        
        # Vérification du profil enseignant
        professeur = Professeur.query.filter_by(
            utilisateur_id=current_user.id,
            ecole_id=ecole_courante.id
        ).first()
        
        if not professeur:
            flash(
                "❌ Profil enseignant introuvable pour cette école.", 
                "danger"
            )
            return redirect(url_for('main.index'))
        
        # Récupération des cours assignés
        mes_cours = Cours.query.filter_by(
            professeur_id=professeur.id,
            ecole_id=ecole_courante.id
        ).order_by(Cours.nom).all()
        
        # Calcul des statistiques
        notes_total = sum([
            len(c.notes) for c in mes_cours 
            if hasattr(c, 'notes')
        ])
        cours_total = len(mes_cours)
        professeurs_actifs = 1  # L'enseignant courant
        
        # Sérialisation JSON
        cours_json = [cours_to_dict(c) for c in mes_cours]

    # === RENDU DU TEMPLATE ===
    return render_template(
        'cours.html',
        cours=cours_json,
        form=form,
        delete_form=delete_form,
        professeurs_count=professeurs_actifs,
        notes_count=notes_total,
        cours_count=cours_total,
        ecole_nom=ecole_courante.nom if ecole_courante else "Système"
    )

@main.route('/ajouter_cours', methods=['POST'])
@login_required
@role_required('admin')
def ajouter_cours():
    ecole_courante = get_ecole_courante()
    form = CoursForm()

    # Choix restreints à l'école courante
    form.professeur_id.choices = [
        (p.id, f"{p.prenom} {p.nom}") 
        for p in Professeur.query.filter_by(ecole_id=ecole_courante.id).order_by(Professeur.nom).all()
    ]
    form.classe_id.choices = [
        (c.id, f"{c.nom} ({c.niveau})") 
        for c in Classe.query.filter_by(ecole_id=ecole_courante.id).order_by(Classe.nom).all()
    ]

    if form.validate_on_submit():
        try:
            # Vérification stricte dans l'école courante
            prof = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_courante.id).first()
            classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=ecole_courante.id).first()

            if not prof or not classe:
                flash("Le professeur ou la classe n’appartient pas à votre école.", "danger")
                return redirect(url_for('main.cours'))

            doublon = Cours.query.filter_by(
                nom=form.nom.data,
                classe_id=form.classe_id.data,
                ecole_id=ecole_courante.id
            ).first()
            if doublon:
                flash("Un cours avec ce nom existe déjà pour cette classe.", "danger")
                return redirect(url_for('main.cours'))

            nouveau_cours = Cours(
                nom=form.nom.data,
                description=form.description.data,
                coefficient=form.coefficient.data,
                professeur_id=prof.id,
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
            flash("Erreur inattendue lors de l’ajout du cours.", "danger")
            current_app.logger.error(f"Erreur ajout cours: {e}")

    else:
        flash("Le formulaire contient des erreurs.", "warning")

    return redirect(url_for('main.cours'))

@main.route('/cours/<int:id>')
@login_required
@role_required('admin', 'enseignant')
def cours_details(id):
    cours = Cours.query.options(
        joinedload(Cours.professeur),
        joinedload(Cours.notes).joinedload(Note.eleve)
    ).get_or_404(id)

    if not check_ecole_access(cours, "cours"):
        if current_user.role == 'enseignant':
            return redirect(url_for('main.enseignant_dashboard'))
        return redirect(url_for('main.profile'))

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
    classes = Classe.query.filter_by(ecole_id=ecole_courante.id).order_by(Classe.nom).all()
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in professeurs]
    form.classe_id.choices = [(c.id, f"{c.nom} ({c.niveau})") for c in classes]

    if request.method == 'GET':
        form.professeur_id.data = cours.professeur_id
        form.classe_id.data = cours.classe_id

    if form.validate_on_submit():
        professeur = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_courante.id).first()
        classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=ecole_courante.id).first()
        if not professeur or not classe:
            flash("Le professeur ou la classe n'appartient pas à votre école.", "danger")
            return redirect(url_for('main.modifier_cours', id=cours.id))

        cours.nom = form.nom.data
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
@role_required('admin', 'enseignant')
def export_notes(id):
    """Export des notes d'un cours spécifique en Excel"""
    cours = Cours.query.options(joinedload(Cours.notes).joinedload(Note.eleve)).filter_by(
        id=id,
        ecole_id=current_user.ecole_id
    ).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

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
@role_required('admin', 'enseignant')
def import_notes_excel(id):
    """Import de notes depuis Excel/CSV avec historique minimal."""
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

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
                    erreurs.append(f"Ligne {index+2}: Élève non trouvé ({prenom or '?'} {nom or '?'})")
                    continue

                if eleve.ecole_id != cours.ecole_id:
                    erreurs.append(f"Ligne {index+2}: Élève associé à une autre école")
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

                # Ajout / mise à jour
                note = Note.query.filter_by(cours_id=id, eleve_id=eleve.id).first()
                if note:
                    if note.annee_id and note.annee_id != annee.id:
                        erreurs.append(
                            f"Ligne {index+2}: Note existante associée à une autre année scolaire"
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
@role_required('admin', 'enseignant')
def modele_import_notes(id):
    """Téléchargement d'un modèle d'importation de notes (Excel ou CSV)"""

    format_fichier = request.args.get('format', 'excel').lower()
    cours = Cours.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()

    # Vérification d'accès pour les enseignants
    if current_user.role == 'enseignant' and cours.professeur_id != current_user.id:
        flash("Accès non autorisé à ce cours.", "danger")
        return redirect(url_for('main.enseignant_dashboard'))

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
        # Deux feuilles identiques pour donner un choix à l’utilisateur
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
@role_required('admin', 'enseignant', 'super_admin')
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
@login_required
@role_required('admin')
def supprimer_cours(id):
    # ✅ Sécurisation multi-écoles
    cours = filtre_par_ecole(Cours.query, Cours).filter_by(id=id).first_or_404()

    try:
        # Vérifier s'il y a des notes ou absences associées
        if cours.notes or cours.absences:
            flash("Impossible de supprimer ce cours car il a des données associées.", "danger")
            return redirect(url_for('main.cours'))

        ancienne_valeur = f"Cours: {cours.nom} (Prof: {cours.professeur_id}, Classe: {cours.classe_id})"

        db.session.delete(cours)
        db.session.commit()

        # ✅ Journalisation
        current_app.log_correction(
            action="suppression_cours",
            description=f"Cours supprimé : {cours.nom}",
            ecole_id=cours.ecole_id,
            cible_type="cours",
            cible_id=id,
            ancienne_valeur=ancienne_valeur,
            nouvelle_valeur=None,
            niveau="info"
        )

        flash("Cours supprimé avec succès.", "success")
        return redirect(url_for('main.cours'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression cours {id}: {e}")
        flash("Erreur inattendue lors de la suppression du cours.", "danger")
        return redirect(url_for('main.cours'))
