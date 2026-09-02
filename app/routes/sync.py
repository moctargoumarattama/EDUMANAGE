from . import main
from .common import (
    Absence,
    AnneeScolaire,
    Classe,
    Eleve,
    Inscription,
    Note,
    Paiement,
    current_user,
    datetime,
    db,
    joinedload,
    jsonify,
    login_required,
    render_template,
    render_template_string,
    request,
    send_from_directory,
)


@main.route("/sync-hors-ligne")
def sync_hors_ligne():
    return render_template("sync_hors_ligne.html")

@main.route('/api/sync', methods=['POST'])
@login_required
def api_sync():
    """API pour synchroniser les données hors ligne"""
    try:
        # Vérifier le Content-Type
        if not request.is_json:
            return jsonify({
                'success': False, 
                'message': 'Content-Type doit être application/json'
            }), 400

        data = request.get_json()
        print(f"📥 Données reçues: {data}")
        print(f"📥 Type de données: {type(data)}")
        print(f"📥 Nombre d'éléments: {len(data) if data else 0}")
        
        if not data:
            return jsonify({
                'success': False, 
                'message': 'Aucune donnée reçue'
            }), 400

        # Vérifier que data est une liste
        if not isinstance(data, list):
            return jsonify({
                'success': False,
                'message': 'Les données doivent être un tableau'
            }), 400

        processed_count = 0
        errors = []

        for index, item in enumerate(data):
            try:
                if not isinstance(item, dict):
                    errors.append(f"Élément {index}: format invalide")
                    continue

                item_type = item.get('type')
                print(f"🔄 Traitement élément {index}: type={item_type}")

                if item_type == 'note':
                    # Vérifier les champs requis
                    required_fields = ['eleve_id', 'cours_id', 'valeur', 'date_evaluation']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Note {index}: champs manquants {missing}")
                        continue

                    # ✅ CONVERTIR LA DATE
                    date_eval = item.get('date_evaluation')
                    if isinstance(date_eval, str):
                        try:
                            # Format: '2025-11-05 05:25:04' ou '2025-11-05T05:25:04'
                            date_eval = datetime.strptime(
                                date_eval.replace('T', ' ')[:19], 
                                '%Y-%m-%d %H:%M:%S'
                            )
                        except ValueError as e:
                            errors.append(f"Note {index}: format de date invalide ({e})")
                            continue

                    # Vérifier si la note existe déjà
                    existing_note = Note.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        cours_id=item.get('cours_id'),
                        date_evaluation=date_eval,
                        type_evaluation=item.get('type_evaluation')
                    ).first()
                    
                    if not existing_note:
                        note = Note(
                            valeur=float(item.get('valeur')),
                            coefficient=float(item.get('coefficient', 1)),
                            type_evaluation=item.get('type_evaluation'),
                            periode=item.get('periode'),
                            eleve_id=int(item.get('eleve_id')),
                            cours_id=int(item.get('cours_id')),
                            date_evaluation=date_eval  # ✅ Objet datetime
                        )
                        db.session.add(note)
                        processed_count += 1
                        print(f"✅ Note ajoutée pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Note déjà existante pour l'élève {item.get('eleve_id')}")

                elif item_type == 'absence':
                    required_fields = ['eleve_id', 'cours_id', 'date_absence']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Absence {index}: champs manquants {missing}")
                        continue

                    # ✅ CONVERTIR LA DATE
                    date_abs = item.get('date_absence')
                    if isinstance(date_abs, str):
                        try:
                            # Format: '2025-11-05'
                            date_abs = datetime.strptime(date_abs, '%Y-%m-%d').date()
                        except ValueError as e:
                            errors.append(f"Absence {index}: format de date invalide ({e})")
                            continue

                    existing_absence = Absence.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        cours_id=item.get('cours_id'),
                        date_absence=date_abs
                    ).first()
                    
                    if not existing_absence:
                        absence = Absence(
                            date_absence=date_abs,  # ✅ Objet date
                            motif=item.get('motif'),
                            justifiee=bool(item.get('justifiee', False)),
                            eleve_id=int(item.get('eleve_id')),
                            cours_id=int(item.get('cours_id'))
                        )
                        db.session.add(absence)
                        processed_count += 1
                        print(f"✅ Absence ajoutée pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Absence déjà existante pour l'élève {item.get('eleve_id')}")

                elif item_type == 'paiement':
                    required_fields = ['eleve_id', 'montant', 'mois', 'annee']
                    missing = [f for f in required_fields if not item.get(f)]
                    if missing:
                        errors.append(f"Paiement {index}: champs manquants {missing}")
                        continue

                    existing_paiement = Paiement.query.filter_by(
                        eleve_id=item.get('eleve_id'),
                        mois=item.get('mois'),
                        annee=item.get('annee'),
                        reference=item.get('reference')
                    ).first()
                    
                    if not existing_paiement:
                        paiement = Paiement(
                            montant=float(item.get('montant')),
                            mois=int(item.get('mois')),
                            annee=int(item.get('annee')),
                            mode_paiement=item.get('mode_paiement'),
                            reference=item.get('reference'),
                            eleve_id=int(item.get('eleve_id'))
                        )
                        db.session.add(paiement)
                        processed_count += 1
                        print(f"✅ Paiement ajouté pour l'élève {item.get('eleve_id')}")
                    else:
                        print(f"⚠️ Paiement déjà existant pour l'élève {item.get('eleve_id')}")

                elif item_type == 'test':
                    print("🔧 Test reçu - ignoré")
                    continue
                    
                else:
                    errors.append(f"Élément {index}: type inconnu ({item_type})")
                    print(f"❌ Type inconnu: {item_type}")

            except Exception as e:
                error_msg = f"Élément {index}: {str(e)}"
                errors.append(error_msg)
                print(f"❌ Erreur sur l'élément {index}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        # Commit seulement s'il y a des données traitées
        if processed_count > 0:
            db.session.commit()
            print(f"✅ Synchronisation terminée: {processed_count} éléments traités")

        response_data = {
            'success': True,
            'message': f'{processed_count} élément(s) synchronisé(s) avec succès',
            'processed': processed_count,
            'total': len(data)
        }
        
        if errors:
            response_data['errors'] = errors
            response_data['message'] = f'{processed_count} élément(s) synchronisé(s), {len(errors)} erreur(s)'

        return jsonify(response_data), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Erreur générale de synchronisation: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Erreur de synchronisation: {str(e)}'
        }), 500

@main.route('/service-worker.js')
def service_worker():
    """Servir le Service Worker"""
    return send_from_directory('static', 'service-worker.js', mimetype='application/javascript')

@main.route('/offline')
def offline_page():
    """Page affichée quand l'utilisateur est hors ligne"""
    offline_html = """
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Hors ligne</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .offline-container {
                text-align: center;
                color: white;
                padding: 2rem;
            }
            .offline-icon {
                font-size: 5rem;
                margin-bottom: 2rem;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.1); }
            }
        </style>
    </head>
    <body>
        <div class="offline-container">
            <div class="offline-icon">
                <i class="fas fa-wifi-slash"></i>
            </div>
            <h1 class="mb-3">Vous êtes hors ligne</h1>
            <p class="lead mb-4">
                Vérifiez votre connexion Internet pour continuer.
            </p>
            <p class="text-white-50">
                Vos données seront automatiquement synchronisées dès que vous serez reconnecté.
            </p>
            <button class="btn btn-light mt-4" onclick="location.reload()">
                <i class="fas fa-sync-alt me-2"></i>
                Réessayer
            </button>
        </div>
        
        <script>
            // Recharger automatiquement quand la connexion revient
            window.addEventListener('online', function() {
                location.reload();
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(offline_html)

@main.route("/inscriptions")
@login_required
def voir_inscriptions():
    classe_filtre = request.args.get("classe")
    annee_filtre = request.args.get("annee")

    inscriptions_query = Inscription.query.options(
        joinedload(Inscription.eleve).joinedload(Eleve.parent),
        joinedload(Inscription.classe),
        joinedload(Inscription.eleve).joinedload(Eleve.notes).joinedload(Note.cours),
        joinedload(Inscription.annee_scolaire)
    ).join(Eleve).filter(Eleve.ecole_id == current_user.ecole_id)

    if classe_filtre:
        inscriptions_query = inscriptions_query.join(Classe).filter(Classe.nom == classe_filtre)

    if annee_filtre:
        inscriptions_query = inscriptions_query.join(AnneeScolaire).filter(AnneeScolaire.nom == annee_filtre)

    inscriptions_raw = inscriptions_query.all()

    classes_dict = {}
    classes_set = set()
    annees_set = set()

    for ins in inscriptions_raw:
        eleve = ins.eleve
        classe = ins.classe

        if not eleve or not classe:
            continue

        classes_set.add(classe.nom)

        if ins.annee_scolaire:
            annees_set.add(ins.annee_scolaire)

        # Calcul de la première année de l'élève dans l'école
        if eleve.inscriptions:
            premiere_inscription = min(
                [i for i in eleve.inscriptions if i.annee_scolaire],
                key=lambda i: i.annee_scolaire.date_debut,
                default=None
            )
            annee_premiere_ecole = premiere_inscription.annee_scolaire.nom if premiere_inscription else "N/A"
        else:
            annee_premiere_ecole = "N/A"

        ins_data = {
            "id": ins.id,
            "eleve_prenom": eleve.prenom,
            "eleve_nom": eleve.nom,
            "classe_nom": classe.nom,
            "annee_scolaire": ins.annee_scolaire.nom if ins.annee_scolaire else "N/A",
            "parent_nom": eleve.parent.nom if eleve.parent else "N/A",
            "annee_premiere_ecole": annee_premiere_ecole,
            "notes": [
                {
                    "cours_nom": note.cours.nom if note.cours else "N/A",
                    "valeur": note.valeur,
                    "periode": note.periode
                } for note in eleve.notes
            ] if eleve.notes else []
        }

        classes_dict.setdefault(classe.nom, []).append(ins_data)

    classes = sorted(classes_set)
    annees = sorted(annees_set, key=lambda a: a.date_debut)

    return render_template(
        "inscriptions.html",
        classes_dict=classes_dict,
        classes=classes,
        annees=annees,
        classe_filtre=classe_filtre,
        annee_filtre=annee_filtre
    )

# SECURITY TODO: endpoint JSON sans @login_required ni filtre ecole_id explicite.
@main.route("/recherche_json")
def recherche_json():
    inscription_id = request.args.get("inscription_id", type=int)
    if not inscription_id:
        return {"error": "inscription_id manquant"}, 400

    ins = Inscription.query.get(inscription_id)
    if not ins:
        return {"error": "Inscription introuvable"}, 404

    eleve = ins.eleve

    # Calculer la première année scolaire de l'élève
    annee_premiere_ecole = "N/A"
    if eleve and eleve.inscriptions:
        premiere_inscription = min(
            [i for i in eleve.inscriptions if i.annee_scolaire],
            key=lambda i: i.annee_scolaire.date_debut,
            default=None
        )
        if premiere_inscription and premiere_inscription.annee_scolaire:
            annee_premiere_ecole = premiere_inscription.annee_scolaire.nom

    return {
        "id": ins.id,
        "eleve_prenom": eleve.prenom if eleve else None,
        "eleve_nom": eleve.nom if eleve else None,
        "classe": ins.classe.nom if ins.classe else None,
        "annee_scolaire": ins.annee_scolaire.nom if ins.annee_scolaire else None,
        "parent_nom": eleve.parent.nom if eleve and eleve.parent else None,
        "annee_premiere_ecole": annee_premiere_ecole,
        "notes": [
            {
                "cours": note.cours.nom if note.cours else None,
                "valeur": note.valeur,
                "periode": note.periode
            }
            for note in eleve.notes
        ]
        if eleve and eleve.notes
        else []
    }
