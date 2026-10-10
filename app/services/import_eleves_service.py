"""
Service d'importation Excel des élèves pour KLASORA — Phase 5I.

Gère la génération du modèle Excel, la prévisualisation/validation en 2 étapes,
l'anti-doublon sécurisé et l'inscription dans l'année consultée.
"""
from datetime import datetime, date
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import json
import os
import tempfile

from app import db
from app.models import AnneeScolaire, Classe, Eleve, Inscription, Utilisateur
from app.services.classes_annuelles import get_classes_ouvertes_annee
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.eleves import cle_identite_eleve, trouver_eleves_identiques
from app.services.matricule_service import verrouiller_ecole
from app.services.phone_numbers import cles_telephone_equivalentes, normaliser_telephone_international
from app.access_codes import generate_access_code


def _parent_import(ecole_id, telephone, nom_parent=None, enfant_nom=None):
    """Rattache ou crée un compte parent à partir d'un téléphone importé."""
    telephone = normaliser_telephone_international(telephone)
    if not telephone:
        return None, None
    cles = cles_telephone_equivalentes(telephone)
    parent = next((p for p in Utilisateur.query.filter_by(ecole_id=ecole_id, role='parent').all()
                   if cles_telephone_equivalentes(p.telephone) & cles), None)
    if not parent:
        parent = Utilisateur(
            nom=(nom_parent or f"Parent {enfant_nom or ''}").strip() or "Parent",
            prenom=None, email=None, telephone=telephone, role='parent', ecole_id=ecole_id,
        )
        parent.set_mot_de_passe(generate_access_code())
        db.session.add(parent)
        db.session.flush()
    return parent, telephone


def _get_temp_import_filepath(token: str) -> str:
    temp_dir = os.path.join(tempfile.gettempdir(), "klasora_imports")
    os.makedirs(temp_dir, exist_ok=True)
    return os.path.join(temp_dir, f"import_{token}.json")


def stocker_preview_import(token: str, data: dict):
    """Stocke le résultat de prévisualisation dans un fichier temporaire serveur (hors cookie)."""
    filepath = _get_temp_import_filepath(token)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def recuperer_preview_import(token: str) -> dict | None:
    """Récupère le résultat de prévisualisation depuis le fichier temporaire serveur."""
    filepath = _get_temp_import_filepath(token)
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def supprimer_preview_import(token: str):
    """Supprime le fichier temporaire de prévisualisation après utilisation."""
    filepath = _get_temp_import_filepath(token)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError:
            pass



def generer_modele_excel_eleves() -> io.BytesIO:
    """Génère un fichier modèle Excel (.xlsx) pour l'import des élèves."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import Élèves"
    
    # En-têtes obligatoires et optionnels
    headers = [
        "Nom *",
        "Prénom *",
        "Genre (M/F)",
        "Date de naissance (AAAA-MM-JJ) *",
        "Lieu de naissance",
        "Adresse",
        "Nom du parent",
        "Téléphone parent",
        "Email parent",
        "Classe *",
        "Statut",
        "Nationalité",
        "N° Acte / Jugement",
        "Nom du père",
        "Nom de la mère",
        "Matricule permanent",
    ]
    
    ws.append(headers)
    
    # Exemples indicatifs
    ws.append([
        "MOUSSA", "Ibrahim", "M", "2012-05-15", "Niamey", "Plateau Villa 12",
        "Moussa Abdoulaye", "+22790123456", "moussa.abdoulaye@example.com", "6e A", "actif",
        "Nigérienne", "ACTE-1234/2012", "Moussa Abdoulaye", "Fati Salifou"
    ])
    ws.append([
        "SALIFOU", "Hadiza", "F", "2013-09-20", "Maradi", "Quartier Zongo",
        "Salifou Ousmane", "+22788123456", "hadiza.salifou@example.com", "6e A", "actif",
        "Nigérienne", "JUG-567/2013", "Salifou Ousmane", "Aïchatou Garba"
    ])
    
    # Style de l'en-tête
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    border_thin = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC")
    )
    
    for col_num, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center
    
    # Ajustement des largeurs de colonnes
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 15)
        
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _parse_date(val):
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    val_str = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            pass
    return None


def previsualiser_import_excel(file_stream, ecole_id: int, annee_consultee: AnneeScolaire | None):
    """
    Analyse un fichier Excel et prévisualise l'import sans modifier la BDD.
    Garantit le respect de l'année consultée et l'absence de fusion ambiguë.
    """
    if not annee_consultee:
        return {
            'is_importable': False,
            'erreur_globale': "Aucune année scolaire consultée. Veuillez sélectionner une année scolaire active ou planifiée.",
            'total_lignes': 0, 'valides': 0, 'avertissements': 0, 'erreurs': 0, 'lignes': []
        }
    
    if annee_consultee.statut == "archivee":
        return {
            'is_importable': False,
            'erreur_globale': f"Importation INTERDITE : l'année consultée '{annee_consultee.nom}' est archivée.",
            'total_lignes': 0, 'valides': 0, 'avertissements': 0, 'erreurs': 0, 'lignes': []
        }
    
    try:
        wb = openpyxl.load_workbook(file_stream, data_only=True)
    except Exception as e:
        return {
            'is_importable': False,
            'erreur_globale': f"Fichier Excel invalide ou corrompu : {str(e)}",
            'total_lignes': 0, 'valides': 0, 'avertissements': 0, 'erreurs': 0, 'lignes': []
        }
    
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    
    if not rows or len(rows) < 2:
        return {
            'is_importable': False,
            'erreur_globale': "Le fichier Excel ne contient aucune ligne de données à importer.",
            'total_lignes': 0, 'valides': 0, 'avertissements': 0, 'erreurs': 0, 'lignes': []
        }
    
    if len(rows) > 1001:  # 1 entête + 1000 lignes max
        return {
            'is_importable': False,
            'erreur_globale': "Le fichier dépasse la limite maximale autorisée de 1000 élèves par import.",
            'total_lignes': 0, 'valides': 0, 'avertissements': 0, 'erreurs': 0, 'lignes': []
        }

    # Classes autorisées pour l'année consultée et l'école courante (respect AnneeNiveauConfig)
    classes_ouvertes = get_classes_ouvertes_annee(ecole_id, annee_consultee.id)
    map_classes = {c.nom.strip().lower(): c for c in classes_ouvertes}
    
    # Le matricule représente le dossier permanent, indépendamment des parents.
    eleves_ecole = Eleve.query.filter_by(ecole_id=ecole_id).all()
    eleves_par_identite = {}
    for eleve in eleves_ecole:
        identite = cle_identite_eleve(eleve.nom, eleve.prenom, eleve.date_naissance)
        eleves_par_identite.setdefault(identite, []).append(eleve)
    identites_fichier = {}
    
    lignes_analysees = []
    count_valides = 0
    count_avertissements = 0
    count_erreurs = 0

    for row_idx, row in enumerate(rows[1:], start=2):
        if not any(row):  # Ligne vide
            continue
            
        nom = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
        prenom = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        genre_raw = str(row[2]).strip().upper() if len(row) > 2 and row[2] is not None else "M"
        genre = "F" if genre_raw.startswith("F") else "M"
        
        date_naiss_raw = row[3] if len(row) > 3 else None
        date_naissance = _parse_date(date_naiss_raw)
        
        lieu_naissance = str(row[4]).strip() if len(row) > 4 and row[4] is not None else ""
        adresse = str(row[5]).strip() if len(row) > 5 and row[5] is not None else ""
        nom_parent = str(row[6]).strip() if len(row) > 6 and row[6] is not None else ""
        telephone_parent = str(row[7]).strip() if len(row) > 7 and row[7] is not None else ""
        email_parent = str(row[8]).strip().lower() if len(row) > 8 and row[8] is not None else ""
        classe_nom = str(row[9]).strip() if len(row) > 9 and row[9] is not None else ""
        statut = str(row[10]).strip().lower() if len(row) > 10 and row[10] is not None else "actif"
        nationalite = str(row[11]).strip() if len(row) > 11 and row[11] is not None else "Nigérienne"
        numero_acte = str(row[12]).strip() if len(row) > 12 and row[12] is not None else ""
        nom_pere = str(row[13]).strip() if len(row) > 13 and row[13] is not None else ""
        nom_mere = str(row[14]).strip() if len(row) > 14 and row[14] is not None else ""
        matricule = str(row[15]).strip() if len(row) > 15 and row[15] is not None else ""

        errs = []
        warns = []
        action = "Nouveau"
        classe_obj = None
        existing_eleve = None

        # Validation 1 : Nom & Prénom
        if not nom or not prenom:
            errs.append("Nom et prénom sont obligatoires.")
        if not date_naissance:
            errs.append("Une date de naissance valide est obligatoire.")

        # Validation 2 : Classe présente et ouverte dans l'année consultée
        if not classe_nom:
            errs.append("Nom de classe obligatoire.")
        else:
            classe_obj = map_classes.get(classe_nom.lower())
            if not classe_obj:
                errs.append(f"Classe '{classe_nom}' introuvable ou fermée pour l'année {annee_consultee.nom}.")

        # Validation 3 : identité exacte, sans fusion basée sur le contact parent.
        if nom and prenom and date_naissance:
            identite = cle_identite_eleve(nom, prenom, date_naissance)
            if identite in identites_fichier and not (matricule or numero_acte):
                errs.append(f"Doublon de la ligne {identites_fichier[identite]} dans ce fichier.")
                action = "Ignoré (Doublon du fichier)"
            else:
                identites_fichier.setdefault(identite, row_idx)

            candidats_matricule = Eleve.query.filter_by(ecole_id=ecole_id, matricule=matricule).all() if matricule else []
            candidats_acte = Eleve.query.filter_by(ecole_id=ecole_id, numero_acte=numero_acte).all() if numero_acte else []
            candidats = (
                candidats_matricule or candidats_acte
                if (matricule or numero_acte)
                else eleves_par_identite.get(identite, [])
            )
            if matricule and candidats_matricule and any(cle_identite_eleve(c.nom, c.prenom, c.date_naissance) != identite for c in candidats_matricule):
                errs.append(f"Le matricule {matricule} appartient à une autre identité : arbitrage obligatoire.")
            if len(candidats) > 1:
                matricules = ", ".join(e.matricule for e in candidats)
                errs.append(f"Plusieurs dossiers existants pour cette identité ({matricules}). Vérifiez-les avant l'import.")
            elif candidats:
                existing_eleve = candidats[0]
                insc = Inscription.query.filter_by(
                    ecole_id=ecole_id, eleve_id=existing_eleve.id, annee_scolaire_id=annee_consultee.id,
                ).first()
                if insc:
                    warns.append(f"Matricule {existing_eleve.matricule} déjà inscrit pour l'année {annee_consultee.nom} (sera ignoré).")
                    if not errs:
                        action = "Déjà inscrit"
                else:
                    warns.append(f"Dossier existant, matricule {existing_eleve.matricule}. Une nouvelle inscription sera créée.")
                    if not errs:
                        action = "Réinscription"

        if not telephone_parent:
            warns.append("Sans contact tuteur : aucun compte parent ne sera rattaché.")
        status_row = "error" if errs else ("warning" if warns else "valid")
        if errs:
            count_erreurs += 1
        elif warns:
            count_avertissements += 1
            count_valides += 1
        else:
            count_valides += 1

        lignes_analysees.append({
            'row_idx': row_idx,
            'nom': nom,
            'prenom': prenom,
            'genre': genre,
            'date_naissance': date_naissance.strftime('%Y-%m-%d') if date_naissance else None,
            'lieu_naissance': lieu_naissance,
            'adresse': adresse,
            'nom_parent': nom_parent,
            'telephone_parent': telephone_parent,
            'email_parent': email_parent,
            'classe_nom': classe_nom,
            'classe_id': classe_obj.id if classe_obj else None,
            'statut': statut,
            'nationalite': nationalite or "Nigérienne",
            'numero_acte': numero_acte or None,
            'nom_pere': nom_pere or None,
            'nom_mere': nom_mere or None,
            'matricule': matricule or None,
            'parent_status': 'Avec contact tuteur' if telephone_parent else 'Sans contact tuteur',
            'existing_eleve_id': existing_eleve.id if existing_eleve else None,
            'status': status_row,
            'action': action,
            'errors': errs,
            'warnings': warns,
        })

    return {
        'is_importable': count_valides > 0,
        'erreur_globale': None,
        'total_lignes': len(lignes_analysees),
        'valides': count_valides,
        'avertissements': count_avertissements,
        'erreurs': count_erreurs,
        'lignes': lignes_analysees,
    }


def executer_import_excel(lignes_valides: list, ecole_id: int, annee_consultee: AnneeScolaire):
    """
    Exécute l'import effectif des lignes validées dans l'année consultée.
    Créé l'Eleve et l'Inscription associée sans toucher à l'historique passé.
    """
    if not annee_consultee or annee_consultee.statut == "archivee":
        return False, "Impossible d'importer dans une année scolaire archivée.", 0, 0, 0

    crees = 0
    reinscrits = 0
    ignores = 0

    try:
        # Le verrou est conservé jusqu'au commit, y compris durant les relectures.
        verrouiller_ecole(ecole_id)
        identites_traitees = set()
        classes_autorisees = {
            classe.id for classe in get_classes_ouvertes_annee(ecole_id, annee_consultee.id)
        }
        for item in lignes_valides:
            if item.get('status') == 'error':
                ignores += 1
                continue

            classe_id = item.get('classe_id')
            if classe_id not in classes_autorisees:
                ignores += 1
                continue

            date_naiss = _parse_date(item.get('date_naissance'))
            nom = (item.get('nom') or '').strip()
            prenom = (item.get('prenom') or '').strip()
            if not nom or not prenom or not date_naiss:
                ignores += 1
                continue
            identite = cle_identite_eleve(nom, prenom, date_naiss)
            matricule = (item.get('matricule') or '').strip()
            numero_acte = (item.get('numero_acte') or '').strip()
            cle_import = (identite, matricule.casefold(), numero_acte.casefold())
            if cle_import in identites_traitees:
                ignores += 1
                continue
            identites_traitees.add(cle_import)

            # Une autre importation a pu créer le dossier depuis la prévisualisation.
            matricule = (item.get('matricule') or '').strip()
            numero_acte = (item.get('numero_acte') or '').strip()
            if matricule:
                candidats = Eleve.query.filter_by(ecole_id=ecole_id, matricule=matricule).all()
            elif numero_acte:
                candidats = Eleve.query.filter_by(ecole_id=ecole_id, numero_acte=numero_acte).all()
            else:
                candidats = trouver_eleves_identiques(ecole_id, nom, prenom, date_naiss)
                # Sans identifiant permanent, une identité homonyme ne doit
                # jamais être fusionnée silencieusement.
            if len(candidats) > 1:
                ignores += 1
                continue

            if candidats:
                eleve_id = candidats[0].id
                parent_user, telephone_normalise = _parent_import(
                    ecole_id, item.get('telephone_parent'), item.get('nom_parent'), nom
                )
                if parent_user:
                    candidats[0].parent_id = parent_user.id
                    candidats[0].contact_parent = telephone_normalise
                deja_inscrit = Inscription.query.filter_by(
                    ecole_id=ecole_id, eleve_id=eleve_id, annee_scolaire_id=annee_consultee.id,
                ).first()
                if deja_inscrit:
                    ignores += 1
                    continue
                # Élève existant -> créer uniquement l'inscription annuelle
                insc, err = creer_inscription_annuelle(
                    ecole_id=ecole_id,
                    eleve_id=eleve_id,
                    annee_scolaire_id=annee_consultee.id,
                    classe_id=classe_id,
                )
                if not err:
                    reinscrits += 1
                else:
                    ignores += 1
            else:
                # Création d'un nouvel élève
                # Gestion facultative d'un utilisateur Parent
                parent_id = None
                email_parent = item.get('email_parent')
                telephone_parent = normaliser_telephone_international(item.get('telephone_parent'))

                parent_user, telephone_parent = _parent_import(
                    ecole_id, telephone_parent, item.get('nom_parent'), nom
                )
                parent_id = parent_user.id if parent_user else None

                nouveau = Eleve(
                    nom=nom,
                    prenom=prenom,
                    genre=item.get('genre', 'M'),
                    date_naissance=date_naiss,
                    lieu_naissance=item.get('lieu_naissance'),
                    adresse=item.get('adresse'),
                    contact_parent=telephone_parent,
                    email_parent=email_parent,
                    parent_id=parent_id,
                    ecole_id=ecole_id,
                    statut=item.get('statut', 'actif'),
                    annee_premiere_ecole=annee_consultee.date_debut.year,
                    nationalite=item.get('nationalite') or 'Nigérienne',
                    numero_acte=numero_acte or None,
                    nom_pere=item.get('nom_pere'),
                    nom_mere=item.get('nom_mere'),
                    matricule=matricule or None,
                )
                db.session.add(nouveau)
                db.session.flush()

                insc, err = creer_inscription_annuelle(
                    ecole_id=ecole_id,
                    eleve_id=nouveau.id,
                    annee_scolaire_id=annee_consultee.id,
                    classe_id=classe_id,
                )
                if not err:
                    crees += 1
                else:
                    raise ValueError(err)

        db.session.commit()
        return True, "Importation réalisée avec succès.", crees, reinscrits, ignores

    except Exception as e:
        db.session.rollback()
        return False, f"Erreur lors de l'exécution de l'import : {str(e)}", 0, 0, 0

