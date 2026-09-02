from . import main
from .common import (
    AnneeScolaire,
    Cours,
    Eleve,
    Note,
    PeriodeBulletin,
    PeriodeForm,
    bulletins_accessible_pour_parent,
    check_parent_access,
    current_app,
    current_user,
    datetime,
    db,
    flash,
    func,
    get_ecole_filter_query,
    joinedload,
    login_required,
    redirect,
    render_template,
    role_required,
    send_file,
    url_for,
)
from app.services import generer_bulletin_pdf


@main.route('/bulletin_eleve/<int:id>')
@login_required
@role_required('admin', 'enseignant', 'parent')
def bulletin_eleve(id):
    """
    Génère le bulletin PDF d’un élève :
    - Sécurisé par école et rôle.
    - Accessible uniquement aux admins, enseignants et parents autorisés.
    """

    # Vérification d’accès spécifique au parent
    if current_user.role == 'parent':
        # Vérifie que le parent a bien accès à cet élève
        if not check_parent_access(id):
            flash("Accès non autorisé à cet élève.", "danger")
            return redirect(url_for('main.parent_dashboard'))

        # Vérifie qu'une période de bulletin est accessible
        if not bulletins_accessible_pour_parent():
            flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
            return redirect(url_for('main.parent_dashboard'))

    # 🔒 Vérification multi-école
    eleve = Eleve.query.filter_by(id=id, ecole_id=current_user.ecole_id).first()
    if not eleve:
        flash("Élève introuvable ou appartenant à une autre école.", "danger")
        return redirect(url_for('main.profile'))

    ecole = eleve.ecole

    # 🔹 Calcul des moyennes par cours
    moyennes = (
        Note.query.with_entities(
            Cours.nom.label('cours_nom'),
            (func.sum(Note.valeur * Note.coefficient) / func.sum(Note.coefficient)).label('moyenne')
        )
        .join(Cours, Note.cours_id == Cours.id)
        .filter(Note.eleve_id == id, Note.ecole_id == current_user.ecole_id)
        .group_by(Cours.nom)
        .all()
    )

    moyennes_par_cours = {
        m.cours_nom: round(m.moyenne, 2) if m.moyenne else 0
        for m in moyennes
    }

    moyenne_generale = (
        round(sum(moyennes_par_cours.values()) / len(moyennes_par_cours), 2)
        if moyennes_par_cours else 0
    )

    # 🔹 Notes détaillées par cours
    notes = (
        Note.query.options(joinedload(Note.cours))
        .filter_by(eleve_id=id, ecole_id=current_user.ecole_id)
        .order_by(Note.cours_id, Note.date_evaluation.desc())
        .all()
    )

    notes_par_cours = {}
    for note in notes:
        cours_nom = note.cours.nom if note.cours else "Non renseigné"
        notes_par_cours.setdefault(cours_nom, []).append(note)

    # 🔹 Génération du PDF
    try:
        buffer = generer_bulletin_pdf(
            eleve,
            notes_par_cours,
            moyennes_par_cours,
            moyenne_generale,
            logo_path=ecole.logo_path if ecole and ecole.logo_path else None,
            nom_ecole=ecole.nom if ecole else "École non renseignée",
            adresse_ecole=ecole.adresse if ecole else "-",
            contact_ecole=f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-"
        )

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"bulletin_{eleve.prenom}_{eleve.nom}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        current_app.logger.error(f"Erreur lors de la génération du bulletin : {e}")
        flash("Erreur lors de la génération du bulletin PDF.", "danger")
        return redirect(url_for('main.profile'))

@main.route('/bulletins')
@login_required
def bulletins():
    # 🔒 Vérifier l'accès pour les parents
    if current_user.role == 'parent' and not bulletins_accessible_pour_parent():
        flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
        return redirect(url_for('main.parent_dashboard'))  # adapte selon ton projet

    # Récupérer tous les élèves de l'école du user
    eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()

    # Calculer les moyennes pour chaque élève
    eleves_avec_moyennes = []
    for eleve in eleves:
        notes = Note.query.filter_by(eleve_id=eleve.id).all()
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
        else:
            moyenne = 0

        eleves_avec_moyennes.append({
            'eleve': eleve,
            'moyenne': moyenne,
            'notes_count': len(notes)
        })
    
    # Trier par moyenne décroissante
    eleves_avec_moyennes.sort(key=lambda x: x['moyenne'], reverse=True)
    
    # Calculer les stats globales
    if eleves_avec_moyennes:
        moyenne_generale = round(sum(e['moyenne'] for e in eleves_avec_moyennes) / len(eleves_avec_moyennes), 2)
        meilleure_moyenne = max(e['moyenne'] for e in eleves_avec_moyennes)
        taux_reussite = round(sum(1 for e in eleves_avec_moyennes if e['moyenne'] >= 10) / len(eleves_avec_moyennes) * 100, 1)
    else:
        moyenne_generale = 0
        meilleure_moyenne = 0
        taux_reussite = 0
    
    return render_template(
        'bulletins.html',
        eleves=eleves_avec_moyennes,
        moyenne_generale=moyenne_generale,
        meilleure_moyenne=meilleure_moyenne,
        taux_reussite=taux_reussite,
        total_eleves=len(eleves),
        bulletins_accessibles=bulletins_accessible_pour_parent()  # fonctionne maintenant
    )

@main.route('/toggle_periode/<int:id>')
@login_required
@role_required('admin')
def toggle_periode(id):
    periode = PeriodeBulletin.query.get_or_404(id)
    periode.publie = not periode.publie  # on inverse l’état
    if periode.publie:
        periode.date_publication = datetime.utcnow()
    db.session.commit()
    flash(f"Période {periode.nom} {'activée' if periode.publie else 'désactivée'} avec succès.", "success")
    return redirect(url_for('main.gestion_periodes'))

@main.route('/periodes')
@login_required
@role_required('admin')
def gestion_periodes():
    periodes = get_ecole_filter_query(PeriodeBulletin).all()
    return render_template("gestion_periodes.html", periodes=periodes)

@main.route('/activer_periode/<int:id>')
@login_required
@role_required('admin')
def activer_periode(id):
    """Rendre une période active (une seule période active à la fois)"""
    # Désactiver toutes les périodes
    PeriodeBulletin.query.filter_by(ecole_id=current_user.ecole_id).update({'periode_active': False})
    
    # Activer la période sélectionnée
    periode = PeriodeBulletin.query.get_or_404(id)
    periode.periode_active = True
    periode.publie = True  # S'assurer qu'elle est publiée
    periode.date_publication = datetime.utcnow()
    
    db.session.commit()
    flash(f"Période {periode.nom} activée avec succès. Les parents peuvent maintenant accéder aux bulletins.", "success")
    return redirect(url_for('main.gestion_periodes'))

@main.route('/creer_periode', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_periode():
    """Créer une nouvelle période de bulletin"""
    form = PeriodeForm()
    
    # Remplir les choix de l'année scolaire
    form.annee_id.choices = [(a.id, a.nom) for a in AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).all()]
    
    if form.validate_on_submit():
        nom = form.nom.data
        annee_id = form.annee_id.data
        
        # Créer la période
        nouvelle_periode = PeriodeBulletin(
            nom=nom,
            annee_id=annee_id,
            ecole_id=current_user.ecole_id,
            publie=False,
            periode_active=False
        )
        
        db.session.add(nouvelle_periode)
        db.session.commit()
        
        flash(f"Période '{nom}' créée avec succès.", "success")
        return redirect(url_for('main.gestion_periodes'))
    
    # GET - Afficher le formulaire
    return render_template('creer_periode.html', form=form)
