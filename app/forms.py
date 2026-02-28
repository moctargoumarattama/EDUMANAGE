from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, DateField, FloatField, SelectField, TextAreaField, BooleanField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, EqualTo
from datetime import datetime, date
from app.models import Utilisateur, Classe, Professeur, Eleve, Cours
from app.middleware import get_ecole_courante, filtre_par_ecole
from app.utils import get_ecole_filter_query
from app.utils import allowed_file, validate_sort_param
from wtforms import SelectField, StringField, TimeField, SubmitField
from wtforms import SelectMultipleField
from wtforms.validators import DataRequired
from app.models import Ecole, AnneeScolaire
from wtforms import HiddenField

# app/forms.py

# -----------------------
# Création utilisateur
# -----------------------
class CreateUserForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired(), Length(min=2, max=100)])
    prenom = StringField('Prénom', validators=[Optional(), Length(max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Mot de passe', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirmer mot de passe', validators=[DataRequired(), EqualTo('password')])
    role = SelectField('Rôle', choices=[('enseignant', 'Enseignant'), ('parent', 'Parent')], validators=[DataRequired()])

    # Liste des élèves pour rattacher un parent (optionnel)
    eleve_id = SelectField('Élève', coerce=int, choices=[], validate_choice=False)

    submit = SubmitField('Créer utilisateur')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            eleves_query = filtre_par_ecole(Eleve.query, Eleve)
            self.eleve_id.choices = [(0, "--- Aucun élève ---")] + [
                (e.id, f"{e.prenom} {e.nom}") for e in eleves_query.order_by(Eleve.nom).all()
            ]
        else:
            self.eleve_id.choices = [(0, "--- Aucun élève ---")]

# -----------------------
# Formulaire de connexion
# -----------------------
class LoginForm(FlaskForm):
    email = StringField('Email ou téléphone', validators=[DataRequired()])
    mot_de_passe = PasswordField('Mot de passe', validators=[DataRequired()])
    remember = BooleanField('Se souvenir de moi')
    submit = SubmitField('Se connecter')

class ParentLoginForm(FlaskForm):
    nom_eleve = StringField("Nom de l'élève", validators=[DataRequired(), Length(min=2, max=50)])
    prenom_eleve = StringField("Prénom de l'élève", validators=[DataRequired(), Length(min=2, max=50)])
    code_parent = PasswordField("Mot de passe élève/parent", validators=[DataRequired(), Length(min=4, max=20)])
    submit = SubmitField("Se connecter")

# -----------------------
# Formulaire Élève + Création Parent
# -----------------------
class EleveForm(FlaskForm):
    # ---------------- Informations personnelles de l'élève ----------------
    nom = StringField('Nom', validators=[DataRequired(), Length(max=100)])
    prenom = StringField('Prénom', validators=[DataRequired(), Length(max=100)])
    date_naissance = DateField('Date de naissance', format='%Y-%m-%d', validators=[DataRequired()])
    lieu_naissance = StringField('Lieu de naissance', validators=[Optional(), Length(max=100)])
    adresse = StringField('Adresse résidentielle', validators=[Optional(), Length(max=200)])
    
    # ---------------- Informations scolaires ----------------
    classe_id = SelectField('Classe', coerce=int, validators=[DataRequired()])
    frais_annuels = FloatField('Frais annuels (FCFA)', validators=[DataRequired(), NumberRange(min=0)], default=150000)
    
    # ---------------- Parent ----------------
    parent_id = SelectField('Parent existant', coerce=int, choices=[], validate_choice=False)
    parent_nom = StringField('Nom du parent', validators=[Optional(), Length(max=100)])
    parent_prenom = StringField('Prénom du parent', validators=[Optional(), Length(max=100)])
    email_parent = StringField('Email du parent', validators=[Optional(), Email(), Length(max=120)])
    telephone_parent = StringField('Téléphone du parent', validators=[Optional(), Length(max=20)])
    code_parent = StringField('Code parent (laisser vide pour générer automatiquement)', validators=[Optional(), Length(max=10)])
    
    submit = SubmitField('Enregistrer')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()

        # ---------------- Filtrage des parents ----------------
        if ecole:
            parents_query = filtre_par_ecole(Utilisateur.query.filter_by(role='parent'), Utilisateur)
            self.parent_id.choices = [(0, "--- Aucun parent ---")] + [
                (p.id, f"{p.prenom or ''} {p.nom} ({p.email})") for p in parents_query.order_by(Utilisateur.nom).all()
            ]
        else:
            self.parent_id.choices = [(0, "--- Aucun parent ---")]

        # ---------------- Filtrage des classes ----------------
        if ecole:
            classes_query = filtre_par_ecole(Classe.query, Classe)
            self.classe_id.choices = [(c.id, f"{c.nom} ({c.niveau})") for c in classes_query.order_by(Classe.nom).all()]
        else:
            self.classe_id.choices = []

# -----------------------
# Formulaire Professeur
# -----------------------
class ProfesseurForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired(), Length(max=100)])
    prenom = StringField('Prénom', validators=[DataRequired(), Length(max=100)])
    date_naissance = DateField('Date de naissance', validators=[Optional()])
    adresse = StringField('Adresse', validators=[Optional(), Length(max=200)])
    telephone = StringField('Téléphone', validators=[DataRequired(), Length(max=20)])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=100)])
    specialite = StringField('Spécialité', validators=[DataRequired(), Length(max=100)])
    matieres_enseignees = StringField('Matières enseignées', validators=[DataRequired(), Length(max=200)])
    code_prof = StringField("Code d'accès", validators=[Optional()])
    submit = SubmitField('Enregistrer')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            profs_query = filtre_par_ecole(Professeur.query, Professeur)
            self.professeur_id = [(p.id, f"{p.prenom} {p.nom}") for p in profs_query.order_by(Professeur.nom).all()]
        else:
            self.professeur_id = []

# -----------------------
# Formulaire Cours
# -----------------------
class CoursForm(FlaskForm):
    nom = StringField('Nom du cours', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    coefficient = FloatField('Coefficient', default=1.0, validators=[DataRequired()])
    professeur_id = SelectField('Professeur', coerce=int, validators=[DataRequired()])
    classe_id = SelectField('Classe', coerce=int, validators=[DataRequired()])  # <-- nouveau
    submit = SubmitField('Enregistrer le cours')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            profs_query = filtre_par_ecole(Professeur.query, Professeur)
            self.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in profs_query.order_by(Professeur.nom).all()]
        else:
            self.professeur_id.choices = []

# -----------------------
# Formulaire Note
# -----------------------
class NoteForm(FlaskForm):
    eleve_id = SelectField('Élève', coerce=int, validators=[DataRequired()])
    cours_id = SelectField('Cours', coerce=int, validators=[DataRequired()])
    valeur = FloatField('Note', validators=[DataRequired(), NumberRange(min=0, max=20)])
    annee_id = SelectField("Année scolaire", coerce=int, validators=[DataRequired()])
    coefficient = FloatField('Coefficient', default=1.0, validators=[DataRequired()])
    type_evaluation = SelectField('Type d\'évaluation', choices=[
        ('Devoir', 'Devoir'), ('Composition', 'Composition'),
        ('Interrogation', 'Interrogation'), ('Projet', 'Projet')
    ], validators=[DataRequired()])
    periode = SelectField('Période', choices=[
        ('Trimestre 1', 'Trimestre 1'), ('Trimestre 2', 'Trimestre 2'),
        ('Trimestre 3', 'Trimestre 3'), ('Semestre 1', 'Semestre 1'),
        ('Semestre 2', 'Semestre 2'), ('Annuelle', 'Annuelle')
    ], default='Trimestre 1', validators=[DataRequired()])
    submit = SubmitField('Enregistrer la note')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()

        # --- Élèves ---
        if ecole:
            eleves_query = filtre_par_ecole(Eleve.query, Eleve)
            eleves_choices = [(e.id, f"{e.prenom} {e.nom} - {e.classe.nom}") for e in eleves_query.order_by(Eleve.nom).all()]
            self.eleve_id.choices = eleves_choices or [(0, "--- Aucun élève disponible ---")]

            # --- Cours ---
            cours_query = filtre_par_ecole(Cours.query, Cours)
            cours_choices = [(c.id, f"{c.nom} ({c.classe.nom})") for c in cours_query.order_by(Cours.nom).all()]
            self.cours_id.choices = cours_choices or [(0, "--- Aucun cours disponible ---")]

            # --- Années scolaires ---
            annees_query = AnneeScolaire.query.order_by(AnneeScolaire.nom.desc()).all()
            annees_choices = [(a.id, a.nom) for a in annees_query if a.active]
            self.annee_id.choices = annees_choices or [(0, "--- Aucune année active ---")]
        else:
            self.eleve_id.choices = [(0, "--- Aucun élève ---")]
            self.cours_id.choices = [(0, "--- Aucun cours ---")]
            self.annee_id.choices = [(0, "--- Aucune année ---")]


# -----------------------
# Formulaire Paiement
# -----------------------
class PaiementForm(FlaskForm):
    eleve_id = SelectField('Élève', coerce=int, validators=[DataRequired()])
    montant = FloatField('Montant (FCFA)', validators=[DataRequired(), NumberRange(min=0)])
    mois = SelectField('Mois', choices=[
        ('Janvier', 'Janvier'), ('Février', 'Février'), ('Mars', 'Mars'), ('Avril', 'Avril'),
        ('Mai', 'Mai'), ('Juin', 'Juin'), ('Juillet', 'Juillet'), ('Août', 'Août'),
        ('Septembre', 'Septembre'), ('Octobre', 'Octobre'), ('Novembre', 'Novembre'), ('Décembre', 'Décembre')
    ], validators=[DataRequired()])
    annee = IntegerField('Année', default=datetime.now().year, validators=[DataRequired()])
    mode_paiement = SelectField('Mode de paiement', choices=[
        ('espèces', 'Espèces'), ('mobile_money', 'Mobile Money'), ('virement', 'Virement bancaire')
    ], validators=[DataRequired()])
    reference = StringField('Référence', validators=[Optional()])
    submit = SubmitField('Enregistrer le paiement')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            eleves_query = filtre_par_ecole(Eleve.query, Eleve)
            self.eleve_id.choices = [(e.id, f"{e.prenom} {e.nom}") for e in eleves_query.order_by(Eleve.nom).all()]
        else:
            self.eleve_id.choices = []

# -----------------------
# Formulaire Absence
# -----------------------
class AbsenceForm(FlaskForm):
    eleve_id = SelectField('Élève', coerce=int, validators=[DataRequired()])
    cours_id = SelectField('Cours', coerce=int, validators=[Optional()])
    date_absence = DateField('Date d\'absence', default=date.today, validators=[DataRequired()])
    motif = StringField('Motif', validators=[Optional(), Length(max=200)])
    justifiee = BooleanField('Justifiée')
    submit = SubmitField('Enregistrer l\'absence')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            eleves_query = filtre_par_ecole(Eleve.query, Eleve)
            self.eleve_id.choices = [(e.id, f"{e.prenom} {e.nom}") for e in eleves_query.order_by(Eleve.nom).all()]
            cours_query = filtre_par_ecole(Cours.query, Cours)
            self.cours_id.choices = [(c.id, c.nom) for c in cours_query.order_by(Cours.nom).all()]
        else:
            self.eleve_id.choices = []
            self.cours_id.choices = []

# -----------------------
# Formulaire réinitialisation mot de passe
# -----------------------
class ResetPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Réinitialiser le mot de passe')

class ClasseForm(FlaskForm):
    nom = StringField("Nom de la classe", validators=[DataRequired()])
    niveau = SelectField("Niveau", choices=[
        ("6eme", "6ème"), 
        ("5eme", "5ème"), 
        ("4eme", "4ème"), 
        ("3eme", "3ème"),
        ("2nde", "2nde"),
        ("1ere", "1ère"),
        ("terminale", "Terminale")
    ], validators=[DataRequired()])
    effectif = IntegerField("Nombre d'élèves", validators=[DataRequired(), NumberRange(min=1)])
    salle = StringField("Salle", validators=[Optional()])
    professeur_principal_id = SelectField("Professeur principal", coerce=int, validators=[Optional()])

    # ✅ Nouveau champ : Année scolaire
    annee_scolaire_id = SelectField("Année scolaire", coerce=int, validators=[DataRequired()])

    submit = SubmitField("Enregistrer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        if ecole:
            # Professeurs
            profs_query = filtre_par_ecole(Professeur.query, Professeur)
            self.professeur_principal_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in profs_query.order_by(Professeur.nom).all()]

            # Années scolaires
            from app.models import AnneeScolaire
            annees_query = AnneeScolaire.query.filter_by(ecole_id=ecole.id).order_by(AnneeScolaire.id.desc())
            self.annee_scolaire_id.choices = [(a.id, a.nom) for a in annees_query.all()]

            # Pré-sélection année active
            annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole.id, statut='active').first()
            if annee_active:
                self.annee_scolaire_id.data = annee_active.id

        else:
            self.professeur_principal_id.choices = []
            self.annee_scolaire_id.choices = []
# -----------------------
# Formulaire Ecole
# -----------------------
class EcoleForm(FlaskForm):
    nom = StringField('Nom de l\'école', validators=[DataRequired(), Length(max=200)])
    adresse = StringField('Adresse', validators=[Optional(), Length(max=300)])
    telephone = StringField('Téléphone', validators=[Optional(), Length(max=20)])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=120)])
    directeur = StringField('Directeur', validators=[Optional(), Length(max=100)])
    submit = SubmitField('Enregistrer')

# -----------------------
# Formulaire choix école
# -----------------------
class ChoisirEcoleForm(FlaskForm):
    ecole_id = SelectField('École', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Choisir cette école')





class AjouterEmploiForm(FlaskForm):
    classe_id = SelectField("Classe", coerce=int, validators=[DataRequired()])  # Ajouter coerce=int
    professeur_id = SelectField("Professeur", coerce=int, validators=[DataRequired()])
    cours_id = SelectField("Cours", coerce=int, validators=[DataRequired()])
    jour = SelectField("Jour", choices=[
        ('Lundi', 'Lundi'), ('Mardi', 'Mardi'), ('Mercredi', 'Mercredi'),
        ('Jeudi', 'Jeudi'), ('Vendredi', 'Vendredi'), ('Samedi', 'Samedi')
    ], validators=[DataRequired()])
    salle = StringField("Salle")
    heure_debut = TimeField("Heure Début", validators=[DataRequired()])
    heure_fin = TimeField("Heure Fin", validators=[DataRequired()])
    submit = SubmitField("Ajouter")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ecole = get_ecole_courante()
        
        # Classes filtrées par école
        if ecole:
            classes_query = filtre_par_ecole(Classe.query, Classe)
            self.classe_id.choices = [(c.id, f"{c.nom} ({c.niveau})") for c in classes_query.order_by(Classe.nom).all()]
            
            profs_query = filtre_par_ecole(Professeur.query, Professeur)
            self.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in profs_query.order_by(Professeur.nom).all()]
            
            cours_query = filtre_par_ecole(Cours.query, Cours)
            self.cours_id.choices = [(c.id, c.nom) for c in cours_query.order_by(Cours.nom).all()]
        else:
            self.classe_id.choices = []
            self.professeur_id.choices = []
            self.cours_id.choices = []

class DeleteForm(FlaskForm):
    submit = SubmitField("Supprimer")
    
    
    
class GererEcolesForm(FlaskForm):
    # Pas de champ ecoles ici puisque vous utilisez des checkboxes manuelles
    submit = SubmitField('Enregistrer')
    
# Formulaire pour demander le lien de réinitialisation
class RequestResetPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Envoyer le lien')

# Formulaire pour réinitialiser le mot de passe
class ResetPasswordConfirmForm(FlaskForm):
    new_password = PasswordField('Nouveau mot de passe', validators=[
        DataRequired(),
        Length(min=6, message='Le mot de passe doit contenir au moins 6 caractères')
    ])
    confirm_password = PasswordField('Confirmer le mot de passe', validators=[
        DataRequired(),
        EqualTo('new_password', message='Les mots de passe ne correspondent pas')
    ])
    submit = SubmitField('Réinitialiser le mot de passe')
    
    
    
    
class BackupSchoolForm(FlaskForm):
    ecole_id = SelectField("École", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Créer la sauvegarde")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remplir dynamiquement la liste des écoles
        self.ecole_id.choices = [(e.id, e.nom) for e in get_ecole_filter_query(Ecole).all()]
        

class CSRFForm(FlaskForm):
    pass  # Seul le token CSRF est nécessaire






class AssignerClassesForm(FlaskForm):
    classes = SelectMultipleField(
        'Classes', 
        coerce=int,  # On récupère les ids des classes comme entiers
        validators=[DataRequired(message="Veuillez sélectionner au moins une classe.")]
    )
    submit = SubmitField("Enregistrer")
    
    
    
class PeriodeForm(FlaskForm):
    nom = StringField('Nom de la période', validators=[DataRequired()])
    annee_id = SelectField('Année scolaire', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Créer la période') 
