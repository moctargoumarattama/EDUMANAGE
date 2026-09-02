from flask_login import UserMixin
from . import db
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
import random
import string
from sqlalchemy import event
from datetime import datetime

# app/models.py
# -----------------------
# Table de liaison pour les gestionnaires multi-écoles
# -----------------------
gestion_ecole = db.Table(
    'gestion_ecole',
    db.Column('utilisateur_id', db.Integer, db.ForeignKey('utilisateur.id'), primary_key=True),
    db.Column('ecole_id', db.Integer, db.ForeignKey('ecole.id'), primary_key=True)
)

# -----------------------
# Table d'association Professeur / Classe
# -----------------------
professeur_classes = db.Table(
    'professeur_classes',
    db.Column('professeur_id', db.Integer, db.ForeignKey('professeur.id'), primary_key=True),
    db.Column('classe_id', db.Integer, db.ForeignKey('classe.id'), primary_key=True),
    db.Column('date_assignation', db.DateTime, server_default=db.func.now()),
    db.Column('ecole_id', db.Integer, db.ForeignKey('ecole.id'), nullable=False)
)

# -----------------------
# Fonction utilitaire
# -----------------------
def generer_code_parent_unique(self):
    """Génère un code parent unique et sauvegarde l'élève."""
    lettres_chiffres = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(lettres_chiffres, k=8))
        if not Eleve.query.filter_by(code_parent=code).first():
            self.code_parent = code
            db.session.commit()
            return code

def assigner_code_parent(self):
    """Si l'élève a un parent et pas de code, génère et sauvegarde le code parent."""
    if self.parent and not self.code_parent:
        return self.generer_code_parent_unique()
    return self.code_parent

# -----------------------
# Année Scolaire
# -----------------------
class AnneeScolaire(db.Model):
    __tablename__ = 'annee_scolaire'
    
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(20), nullable=False)
    date_debut = db.Column(db.Date, nullable=False)
    date_fin = db.Column(db.Date, nullable=False)
    statut = db.Column(db.String(20), default='planifiee')  # planifiee, active, archivee
    
    # Lien avec l'école
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    ecole = db.relationship('Ecole', backref='annees_scolaires')
    __table_args__ = (db.Index('idx_ecole_id', 'ecole_id'),)

    # Relations
    classes = db.relationship('Classe', back_populates='annee_scolaire', lazy=True)
    inscriptions = db.relationship('Inscription', back_populates='annee_scolaire', lazy=True)
    
    # Contrainte unique nom + ecole
    __table_args__ = (
        db.UniqueConstraint('nom', 'ecole_id', name='_annee_ecole_uc'),
        db.Index('idx_ecole_id', 'ecole_id'),
    )
    
    def __repr__(self):
        return f'<AnneeScolaire {self.nom} - {self.ecole.nom if self.ecole else "Sans école"}>'
    
    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "date_debut": self.date_debut.isoformat() if self.date_debut else None,
            "date_fin": self.date_fin.isoformat() if self.date_fin else None,
            "statut": self.statut,
            "ecole_id": self.ecole_id,
            "ecole_nom": self.ecole.nom if self.ecole else None
        }


# -----------------------
# École
# -----------------------
class Ecole(db.Model):
    __tablename__ = 'ecole'
    
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    adresse = db.Column(db.String(300))
    telephone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    directeur = db.Column(db.String(100))
    logo = db.Column(db.String(200), default='default_logo.png')
    statut = db.Column(db.String(20), default='actif')
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    logo_path = db.Column(db.String(200))  # <-- champ existant
    
    # Relations
    utilisateurs = db.relationship(
        'Utilisateur',
        back_populates='ecole',
        cascade="all, delete-orphan",
        lazy=True
    )
    gestionnaires = db.relationship(
        'Utilisateur',
        secondary=gestion_ecole,
        back_populates='ecoles_gerees',
        lazy='dynamic'
    )
    classes = db.relationship('Classe', back_populates='ecole', lazy=True)
    eleves = db.relationship('Eleve', back_populates='ecole', lazy=True)
    professeurs = db.relationship('Professeur', back_populates='ecole', lazy=True)
    
    def __repr__(self):
        return f'<Ecole {self.nom}>'

    # --- Conversion en dictionnaire pour la sauvegarde JSON ---
    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "adresse": self.adresse,
            "telephone": self.telephone,
            "email": self.email,
            "directeur": self.directeur,
            "logo": self.logo,
            "statut": self.statut,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "logo_path": self.logo_path
        }

# -----------------------
# Utilisateur (comptes)
# -----------------------

# -----------------------------------------------------------------------------
# Utilisateur
# -----------------------------------------------------------------------------
class Utilisateur(db.Model, UserMixin):
    __tablename__ = 'utilisateur'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True, nullable=False)
    mot_de_passe = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='parent')
    telephone = db.Column(db.String(20))
    statut = db.Column(db.String(20), default='actif')
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    dernier_acces = db.Column(db.DateTime)

    # nullable=True pour permettre aux super admins de ne pas avoir d'école
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='SET NULL'), nullable=True)
    ecole = db.relationship('Ecole', back_populates='utilisateurs')

    # écoles gérées (admin/gestionnaire)
    ecoles_gerees = db.relationship(
        'Ecole',
        secondary='gestion_ecole',
        back_populates='gestionnaires',
        lazy='dynamic'
    )

    # --- Relations parents/enfants ---
    # IMPORTANT: ne pas utiliser delete-orphan ici pour éviter de supprimer des élèves
    enfants = db.relationship(
        'Eleve',
        back_populates='parent',
        lazy='dynamic',
        foreign_keys='Eleve.parent_id',
        passive_deletes=True
    )

    # --- Relation Professeur 1-1 (un utilisateur peut avoir un profil professeur) ---
    professeur_rel = db.relationship(
        'Professeur',
        back_populates='utilisateur',
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # relation pour cours si nécessaire (vérifier foreign_keys selon ta table Cours)
    cours_enseignes = db.relationship(
        'Cours',
        back_populates='enseignant_utilisateur',
        lazy=True,
        # assume Cours.enseignant_id existe et réfère à Utilisateur.id
        foreign_keys='Cours.enseignant_id'
    )

    # system
    alertes = db.relationship('Alerte', back_populates='utilisateur', lazy=True)
    logs = db.relationship('Log', back_populates='utilisateur', lazy=True)

    # ---------- utilitaires ----------
    def set_mot_de_passe(self, mot_de_passe_plain: str):
        self.mot_de_passe = generate_password_hash(mot_de_passe_plain)

    def check_mot_de_passe(self, mot_de_passe_plain: str) -> bool:
        return check_password_hash(self.mot_de_passe, mot_de_passe_plain)

    def is_admin(self) -> bool:
        return self.role == 'admin'

    def get_professeur(self):
        return self.professeur_rel if self.role in ('enseignant', 'professeur') else None

    def get_enfants(self):
        return self.enfants.all() if self.role == 'parent' else []

    def __repr__(self):
        return f'<Utilisateur {self.nom} ({self.email})>'

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "prenom": self.prenom,
            "email": self.email,
            "role": self.role,
            "telephone": self.telephone,
            "statut": self.statut,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "dernier_acces": self.dernier_acces.isoformat() if self.dernier_acces else None,
            "ecole_id": self.ecole_id
        }

# -----------------------------------------------------------------------------
# Professeur
# -----------------------------------------------------------------------------
class Professeur(db.Model):
    __tablename__ = 'professeur'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    date_naissance = db.Column(db.Date)
    adresse = db.Column(db.String(200))
    telephone = db.Column(db.String(20))
    # IMPORTANT: retirer unique=True pour éviter conflit avec Utilisateur.email
    email = db.Column(db.String(120))
    specialite = db.Column(db.String(100))
    matieres_enseignees = db.Column(db.String(200))
    photo = db.Column(db.String(200), default='default_prof.png')
    date_embauche = db.Column(db.DateTime, default=datetime.utcnow)
    planning = db.Column(db.JSON)
    code_prof = db.Column(db.String(50), unique=True)
    mot_de_passe = db.Column(db.String(200))

    # Utilisateur 1-1 (force la liaison utilisateur <-> professeur)
    utilisateur_id = db.Column(
        db.Integer,
        db.ForeignKey('utilisateur.id', ondelete='CASCADE'),
        nullable=False,
        unique=True
    )

    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False)
    ecole = db.relationship('Ecole', back_populates='professeurs')

    utilisateur = db.relationship('Utilisateur', back_populates='professeur_rel', uselist=False)
    cours = db.relationship('Cours', back_populates='professeur', lazy=True, cascade="all, delete-orphan")
    emplois_du_temps = db.relationship('EmploiTemps', back_populates='professeur', lazy=True, cascade="all, delete-orphan")

    classes_assignees = db.relationship(
        'Classe',
        secondary=professeur_classes,
        back_populates='professeurs_assignes',
        lazy='dynamic'
    )

    def __repr__(self):
        return f'<Professeur {self.prenom} {self.nom}>'

    @staticmethod
    def generer_code(length=6):
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "prenom": self.prenom,
            "date_naissance": self.date_naissance.isoformat() if self.date_naissance else None,
            "adresse": self.adresse,
            "telephone": self.telephone,
            "email": self.email,
            "specialite": self.specialite,
            "matieres_enseignees": self.matieres_enseignees,
            "photo": self.photo,
            "date_embauche": self.date_embauche.isoformat() if self.date_embauche else None,
            "planning": self.planning,
            "code_prof": self.code_prof,
            "utilisateur_id": self.utilisateur_id,
            "ecole_id": self.ecole_id,
            "classes_assignees": [classe.to_dict() for classe in self.classes_assignees.all()]
        }


# -----------------------
# Classe
# -----------------------
class Classe(db.Model):
    __tablename__ = 'classe'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(50), nullable=False)
    niveau = db.Column(db.String(50))
    effectif = db.Column(db.Integer, default=0)
    capacite = db.Column(db.Integer, default=30)

    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    ecole = db.relationship('Ecole', back_populates='classes')
    salle = db.Column(db.String(50))
    professeur_id = db.Column(db.Integer, db.ForeignKey("professeur.id"))
    
    # Lien avec l'année scolaire
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False, default=1)
    annee_scolaire = db.relationship('AnneeScolaire', back_populates='classes')
    
    eleves = db.relationship('Eleve', back_populates='classe', lazy=True, cascade="all, delete-orphan")
    emplois = db.relationship('EmploiTemps', back_populates='classe', lazy=True, cascade="all, delete-orphan")
    cours = db.relationship('Cours', back_populates='classe', lazy=True, cascade="all, delete-orphan")
    capacite_max = db.Column(db.Integer, nullable=False, default=30)
    
    # NOUVELLE RELATION - Professeurs assignés à cette classe
    professeurs_assignes = db.relationship(
        'Professeur', 
        secondary=professeur_classes,
        back_populates='classes_assignees',
        lazy='dynamic'
    )
    
    @property
    def nom_complet(self):
        annee_nom = self.annee_scolaire.nom if self.annee_scolaire else "N/A"
        return f"{self.nom} - {annee_nom}"
    
    @property
    def effectif_reel(self):
        return len(self.eleves)

    def __repr__(self):
        return f'<Classe {self.nom_complet}>'

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "nom_complet": self.nom_complet,
            "niveau": self.niveau,
            "effectif": self.effectif,
            "ecole_id": self.ecole_id,
            "salle": self.salle,
            "professeur_id": self.professeur_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "capacite_max": self.capacite_max,
            "effectif_reel": self.effectif_reel,
            # Ajouter les professeurs assignés
            "professeurs_assignes": [prof.to_dict() for prof in self.professeurs_assignes]
        }
# -----------------------------------------------------------------------------
# Eleve
# -----------------------------------------------------------------------------
class Eleve(db.Model):
    __tablename__ = 'eleve'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    date_naissance = db.Column(db.Date, nullable=False)
    lieu_naissance = db.Column(db.String(100))
    adresse = db.Column(db.String(200))
    telephone = db.Column(db.String(20))
    contact_parent = db.Column(db.String(20))
    email = db.Column(db.String(120))
    email_parent = db.Column(db.String(120))
    genre = db.Column(db.String(1), default='M')
    frais_annuels = db.Column(db.Float, default=150000.0)
    code_parent = db.Column(db.String(10), unique=True, nullable=True)
    photo = db.Column(db.String(200), default='default_eleve.png')
    statut = db.Column(db.String(20), default='actif')
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)

    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False)
    ecole = db.relationship('Ecole', back_populates='eleves')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    classe_id = db.Column(db.Integer, db.ForeignKey('classe.id', ondelete='SET NULL'), nullable=True)
    classe = db.relationship('Classe', back_populates='eleves')

    # parent_id : ondelete SET NULL pour ne pas supprimer un élève si le parent est supprimé
    parent_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id', ondelete='SET NULL'), nullable=True)
    parent = db.relationship('Utilisateur', back_populates='enfants', foreign_keys=[parent_id])

    notes = db.relationship('Note', backref='eleve', lazy=True, cascade="all, delete-orphan")
    paiements = db.relationship('Paiement', backref='eleve', lazy=True, cascade="all, delete-orphan")
    absences = db.relationship('Absence', backref='eleve', lazy=True, cascade="all, delete-orphan")
    alertes = db.relationship('Alerte', back_populates='eleve', lazy=True)
    annee_premiere_ecole = db.Column(db.Integer)

    @staticmethod
    def generer_code_parent(length=8):
        lettres_chiffres = string.ascii_uppercase + string.digits
        return ''.join(random.choices(lettres_chiffres, k=length))

    def total_paye(self):
        return sum(p.montant for p in self.paiements)

    def reste_a_payer(self):
        return max(0, self.frais_annuels - self.total_paye())

    def pourcentage_paye(self):
        return round((self.total_paye() / self.frais_annuels) * 100, 2) if self.frais_annuels else 0

    def moyenne_generale(self):
        if not self.notes:
            return None
        total_pondere = sum(n.valeur * n.coefficient for n in self.notes)
        total_coeff = sum(n.coefficient for n in self.notes)
        return round(total_pondere / total_coeff, 2) if total_coeff > 0 else 0

    def __repr__(self):
        return f'<Élève {self.prenom} {self.nom}>'

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "prenom": self.prenom,
            "date_naissance": self.date_naissance.isoformat() if self.date_naissance else None,
            "lieu_naissance": self.lieu_naissance,
            "adresse": self.adresse,
            "telephone": self.telephone,
            "contact_parent": self.contact_parent,
            "email": self.email,
            "email_parent": self.email_parent,
            "genre": self.genre,
            "frais_annuels": self.frais_annuels,
            "code_parent": self.code_parent,
            "photo": self.photo,
            "statut": self.statut,
            "date_inscription": self.date_inscription.isoformat() if self.date_inscription else None,
            "ecole_id": self.ecole_id,
            "classe_id": self.classe_id,
            "parent_id": self.parent_id,
            "annee_premiere_ecole": self.annee_premiere_ecole
        }
# -----------------------
# Note
# -----------------------
class Note(db.Model):
    __tablename__ = 'note'

    id = db.Column(db.Integer, primary_key=True)
    valeur = db.Column(db.Float, nullable=False)
    coefficient = db.Column(db.Float, default=1.0)
    type_evaluation = db.Column(db.String(50))
    periode = db.Column(db.String(50), default='Trimestre 1')
    date_evaluation = db.Column(db.DateTime, default=datetime.utcnow)
    annee_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'))
    annee = db.relationship('AnneeScolaire', backref='notes')


    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id'), nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey('cours.id'), nullable=False)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'))

    def __repr__(self):
        return f'<Note {self.valeur} (élève {self.eleve_id})>'

    def to_dict(self):
        return {
            "id": self.id,
            "valeur": self.valeur,
            "coefficient": self.coefficient,
            "type_evaluation": self.type_evaluation,
            "periode": self.periode,
            "date_evaluation": self.date_evaluation.isoformat() if self.date_evaluation else None,
            "eleve_id": self.eleve_id,
            "cours_id": self.cours_id,
            "ecole_id": self.ecole_id
        }

# -----------------------
# Paiement
# -----------------------
class Paiement(db.Model):
    __tablename__ = 'paiement'

    id = db.Column(db.Integer, primary_key=True)
    montant = db.Column(db.Float, nullable=False)
    date_paiement = db.Column(db.DateTime, default=datetime.utcnow)
    mois = db.Column(db.String(20), nullable=False)
    annee = db.Column(db.Integer, default=datetime.utcnow().year)
    mode_paiement = db.Column(db.String(30), default='espèces')
    statut = db.Column(db.String(20), default='payé')
    reference = db.Column(db.String(100))
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id'), nullable=False)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'))

    def statut_paiement(self):
        mois_num = {
            'Janvier': 1, 'Février': 2, 'Mars': 3, 'Avril': 4,
            'Mai': 5, 'Juin': 6, 'Juillet': 7, 'Août': 8,
            'Septembre': 9, 'Octobre': 10, 'Novembre': 11, 'Décembre': 12
        }
        if self.mois not in mois_num:
            return "Mois invalide"
        date_limite = date(self.annee, mois_num[self.mois], 10)
        return "OK" if self.date_paiement.date() <= date_limite else "En retard"

    def __repr__(self):
        return f'<Paiement {self.montant} {self.mois}/{self.annee}>'

    def to_dict(self):
        return {
            "id": self.id,
            "montant": self.montant,
            "date_paiement": self.date_paiement.isoformat() if self.date_paiement else None,
            "mois": self.mois,
            "annee": self.annee,
            "mode_paiement": self.mode_paiement,
            "statut": self.statut,
            "reference": self.reference,
            "eleve_id": self.eleve_id,
            "ecole_id": self.ecole_id
        }

# -----------------------
# Absence
# -----------------------
class Absence(db.Model):
    __tablename__ = 'absence'

    id = db.Column(db.Integer, primary_key=True)
    date_absence = db.Column(db.Date, default=date.today, nullable=False)
    motif = db.Column(db.String(200))
    justifiee = db.Column(db.Boolean, default=False)

    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id'), nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey('cours.id'))
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'))

    def __repr__(self):
        return f'<Absence {self.date_absence} - élève {self.eleve_id}>'

    def to_dict(self):
        return {
            "id": self.id,
            "date_absence": self.date_absence.isoformat() if self.date_absence else None,
            "motif": self.motif,
            "justifiee": self.justifiee,
            "eleve_id": self.eleve_id,
            "cours_id": self.cours_id,
            "ecole_id": self.ecole_id
        }

# -----------------------
# Cours
# -----------------------
class Cours(db.Model):
    __tablename__ = 'cours'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    coefficient = db.Column(db.Float, default=1.0)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    classe_id = db.Column(db.Integer, db.ForeignKey('classe.id'))
    professeur_id = db.Column(db.Integer, db.ForeignKey('professeur.id'))
    enseignant_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'))
    
    # Relations existantes
    classe = db.relationship('Classe', back_populates='cours')
    professeur = db.relationship('Professeur', back_populates='cours')
    notes = db.relationship('Note', backref='cours', lazy=True, cascade="all, delete-orphan")
    absences = db.relationship('Absence', backref='cours', lazy=True, cascade="all, delete-orphan")
    emplois_du_temps = db.relationship('EmploiTemps', back_populates='cours', lazy=True, cascade="all, delete-orphan")

    # --- Nouvelle relation avec Utilisateur ---
    enseignant_utilisateur = db.relationship(
        'Utilisateur',
        back_populates='cours_enseignes',
        foreign_keys=[enseignant_id]
    )

    def __repr__(self):
        return f'<Cours {self.nom}>'

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "description": self.description,
            "coefficient": self.coefficient,
            "ecole_id": self.ecole_id,
            "classe_id": self.classe_id,
            "professeur_id": self.professeur_id,
            "enseignant_id": self.enseignant_id
        }

# -----------------------
# Bulletin
# -----------------------
class Bulletin(db.Model):
    __tablename__ = 'bulletin'

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id'), nullable=False)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)  # <- Ajouté
    matiere = db.Column(db.String(100), nullable=False)
    note = db.Column(db.Float, nullable=False)
    annee = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    eleve = db.relationship('Eleve', backref='bulletins')
    ecole = db.relationship('Ecole', backref='bulletins')  # <- Relation pour filtrage facile

    def __repr__(self):
        return f'<Bulletin {self.id} - Eleve {self.eleve_id}>'

    def to_dict(self):
        return {
            "id": self.id,
            "eleve_id": self.eleve_id,
            "ecole_id": self.ecole_id,  # <- Inclure ecole_id
            "matiere": self.matiere,
            "note": self.note,
            "annee": self.annee,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

# -----------------------
# EmploiTemps
# -----------------------
class EmploiTemps(db.Model):
    __tablename__ = 'emploi_temps'

    id = db.Column(db.Integer, primary_key=True)
    professeur_id = db.Column(db.Integer, db.ForeignKey('professeur.id'), nullable=False)
    jour = db.Column(db.String(20), nullable=False)
    heure_debut = db.Column(db.Time, nullable=False)
    heure_fin = db.Column(db.Time, nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey('cours.id'), nullable=False)
    classe_id = db.Column(db.Integer, db.ForeignKey('classe.id'), nullable=False)
    salle = db.Column(db.String(50))
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'))  # ✅ Ajout du lien vers l’école
    professeur = db.relationship('Professeur', back_populates='emplois_du_temps')
    cours = db.relationship('Cours', back_populates='emplois_du_temps')
    classe = db.relationship('Classe', back_populates='emplois')

    def __repr__(self):
        return f'<EmploiTemps {self.jour} {self.heure_debut}-{self.heure_fin}>'

    def to_dict(self):
        return {
            "id": self.id,
            "professeur_id": self.professeur_id,
            "jour": self.jour,
            "heure_debut": self.heure_debut.isoformat() if self.heure_debut else None,
            "heure_fin": self.heure_fin.isoformat() if self.heure_fin else None,
            "cours_id": self.cours_id,
            "classe_id": self.classe_id,
            "salle": self.salle
        }

# -----------------------
# Alerte
# -----------------------
class Alerte(db.Model):
    __tablename__ = 'alerte'

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    titre = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(50))
    lien = db.Column(db.String(200))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_lue = db.Column(db.DateTime, nullable=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id'), nullable=True)
    priorite = db.Column(db.Integer, default=1)

    utilisateur = db.relationship('Utilisateur', back_populates='alertes')
    eleve = db.relationship('Eleve', back_populates='alertes')

    def __repr__(self):
        return f'<Alerte {self.titre} ({self.type})>'

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "titre": self.titre,
            "message": self.message,
            "source": self.source,
            "lien": self.lien,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "date_lue": self.date_lue.isoformat() if self.date_lue else None,
            "utilisateur_id": self.utilisateur_id,
            "eleve_id": self.eleve_id,
            "priorite": self.priorite
        }

# -----------------------
# Logs système
# -----------------------
class Log(db.Model):
    __tablename__ = 'log'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    level = db.Column(db.String(20), nullable=False)
    module = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=True)  # 🔥 ajout important
    ip_address = db.Column(db.String(45))
    
    utilisateur = db.relationship('Utilisateur', back_populates='logs')
    
    def __repr__(self):
        return f'<Log {self.timestamp} {self.level} {self.action}>'

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "module": self.module,
            "action": self.action,
            "details": self.details,
            "utilisateur_id": self.utilisateur_id,
            "ip_address": self.ip_address
        }

# -----------------------
# Paramètres système
# -----------------------
class ParametreSysteme(db.Model):
    __tablename__ = 'parametre_systeme'
    
    id = db.Column(db.Integer, primary_key=True)
    cle = db.Column(db.String(100), unique=True, nullable=False)
    valeur = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    modifiable = db.Column(db.Boolean, default=True)
    
    def __repr__(self):
        return f'<ParametreSysteme {self.cle}={self.valeur}>'

    def to_dict(self):
        return {
            "id": self.id,
            "cle": self.cle,
            "valeur": self.valeur,
            "description": self.description,
            "modifiable": self.modifiable
        }

# -----------------------
# Archives
# -----------------------
class ArchiveNote(db.Model):
    __tablename__ = 'archive_note'
    
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, nullable=False)
    cours_id = db.Column(db.Integer, nullable=False)
    valeur = db.Column(db.Float, nullable=False)
    coefficient = db.Column(db.Float, default=1.0)
    type_evaluation = db.Column(db.String(50))
    periode = db.Column(db.String(50))
    date_evaluation = db.Column(db.DateTime)
    
    # NOUVEAU: Stocker aussi la classe et l'année scolaire
    classe_id = db.Column(db.Integer, nullable=False)
    annee_scolaire_id = db.Column(db.Integer, nullable=False)
    archived_by = db.Column(db.Integer, db.ForeignKey('utilisateur.id'))

    annee_scolaire = db.Column(db.String(20), nullable=False)
    date_archivage = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ArchiveNote {self.eleve_id} {self.cours_id} {self.valeur}>'

    def to_dict(self):
        return {
            "id": self.id,
            "eleve_id": self.eleve_id,
            "cours_id": self.cours_id,
            "valeur": self.valeur,
            "coefficient": self.coefficient,
            "type_evaluation": self.type_evaluation,
            "periode": self.periode,
            "date_evaluation": self.date_evaluation.isoformat() if self.date_evaluation else None,
            "classe_id": self.classe_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "annee_scolaire": self.annee_scolaire,
            "date_archivage": self.date_archivage.isoformat() if self.date_archivage else None
        }

class ArchiveAbsence(db.Model):
    __tablename__ = 'archive_absence'
    
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, nullable=False)
    cours_id = db.Column(db.Integer, nullable=True)
    date_absence = db.Column(db.Date, nullable=False)
    motif = db.Column(db.String(200))
    justifiee = db.Column(db.Boolean, default=False)
    archived_by = db.Column(db.Integer, db.ForeignKey('utilisateur.id'))

    # NOUVEAU: Stocker aussi la classe et l'année scolaire
    classe_id = db.Column(db.Integer, nullable=False)
    annee_scolaire_id = db.Column(db.Integer, nullable=False)
    
    annee_scolaire = db.Column(db.String(20), nullable=False)
    date_archivage = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ArchiveAbsence {self.eleve_id} {self.date_absence}>'

    def to_dict(self):
        return {
            "id": self.id,
            "eleve_id": self.eleve_id,
            "cours_id": self.cours_id,
            "date_absence": self.date_absence.isoformat() if self.date_absence else None,
            "motif": self.motif,
            "justifiee": self.justifiee,
            "classe_id": self.classe_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "annee_scolaire": self.annee_scolaire,
            "date_archivage": self.date_archivage.isoformat() if self.date_archivage else None
        }

# -----------------------
# Synchronisation
# -----------------------
class SyncLog(db.Model):
    __tablename__ = "sync_logs"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.JSON, nullable=False)
    source = db.Column(db.String(50), default="webapp")
    status = db.Column(db.String(20), default="pending")  # pending / processed / error
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<SyncLog {self.id} status={self.status}>"

    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None
        }

# -----------------------
# Inscriptions
# -----------------------
class Inscription(db.Model):
    __tablename__ = "inscriptions"
    
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("eleve.id"), nullable=False)
    classe_id = db.Column(db.Integer, db.ForeignKey("classe.id"), nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey("cours.id"), nullable=True)
    
    # REMPLACER annee_scolaire par annee_scolaire_id
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False)
    annee_scolaire = db.relationship('AnneeScolaire', back_populates='inscriptions')
    
    eleve = db.relationship("Eleve", backref="inscriptions", foreign_keys=[eleve_id])
    classe = db.relationship("Classe", backref="inscriptions", foreign_keys=[classe_id])
    cours = db.relationship("Cours", backref="inscriptions", foreign_keys=[cours_id])

    def to_dict(self):
        return {
            "id": self.id,
            "eleve_id": self.eleve_id,
            "classe_id": self.classe_id,
            "cours_id": self.cours_id,
            "annee_scolaire_id": self.annee_scolaire_id
        }

@event.listens_for(Eleve, "after_insert")
def creer_inscription(mapper, connection, target):
    """Créer automatiquement une inscription quand un élève est ajouté."""
    # Récupérer l'année scolaire active
    annee_active = AnneeScolaire.query.filter_by(
        ecole_id=target.ecole_id,
        statut='active'
    ).first()
    if not annee_active:
        # Créer une année scolaire par défaut si aucune n'existe
        annee_active = AnneeScolaire(
            nom=f"{datetime.now().year}-{datetime.now().year+1}",
            date_debut=date(datetime.now().year, 9, 1),
            date_fin=date(datetime.now().year+1, 7, 31),
            statut='active',
            ecole_id=target.ecole_id
        )
        db.session.add(annee_active)
        db.session.commit()
    
    connection.execute(
        Inscription.__table__.insert().values(
            eleve_id=target.id,
            classe_id=target.classe_id,
            cours_id=None,
            annee_scolaire_id=annee_active.id
        )
    )

# -----------------------
# JournalCorrection
# -----------------------
class JournalCorrection(db.Model):
    __tablename__ = 'journal_corrections'
    
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(50), nullable=False)       # ex: "modification", "suppression"
    description = db.Column(db.String(255), nullable=False) # texte lisible
    
    ancienne_valeur = db.Column(db.Text, nullable=True)
    nouvelle_valeur = db.Column(db.Text, nullable=True)

    cible_type = db.Column(db.String(50), nullable=True)    # "note", "absence", "eleve"
    cible_id = db.Column(db.Integer, nullable=True)

    niveau = db.Column(db.String(20), default="info")       # info, warning, critique

    date = db.Column(db.DateTime, default=datetime.utcnow)

    # Relations
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=True)

    ecole = db.relationship("Ecole", backref="corrections")
    user = db.relationship("Utilisateur", backref="corrections")

    def __repr__(self):
        return f"<JournalCorrection {self.action} - {self.description}>"

    def to_dict(self):
        return {
            "id": self.id,
            "action": self.action,
            "description": self.description,
            "ancienne_valeur": self.ancienne_valeur,
            "nouvelle_valeur": self.nouvelle_valeur,
            "cible_type": self.cible_type,
            "cible_id": self.cible_id,
            "niveau": self.niveau,
            "date": self.date.isoformat() if self.date else None,
            "ecole_id": self.ecole_id,
            "user_id": self.user_id
        }
        
class HistoriqueImport(db.Model):
    __tablename__ = 'historique_import'
    
    id = db.Column(db.Integer, primary_key=True)
    fichier = db.Column(db.String(200), nullable=False)
    date_import = db.Column(db.DateTime, default=datetime.utcnow)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=False)
    
    # Relation vers l'utilisateur qui a fait l'import
    utilisateur = db.relationship('Utilisateur', backref='imports')

    # Propriété pour accéder à l'école via l'utilisateur
    @property
    def ecole(self):
        if self.utilisateur:
            return self.utilisateur.ecole  # suppose que Utilisateur a une relation 'ecole'
        return None

    def __repr__(self):
        return f"<HistoriqueImport {self.fichier} ({self.date_import})>"

    def to_dict(self):
        return {
            "id": self.id,
            "fichier": self.fichier,
            "date_import": self.date_import.isoformat() if self.date_import else None,
            "utilisateur_id": self.utilisateur_id,
            "ecole_id": self.ecole.id if self.ecole else None,
            "ecole_nom": self.ecole.nom if self.ecole else None
        }

        
        
class Presence(db.Model):
    __tablename__ = "presence"  # nom explicite de la table
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("eleve.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date())
    statut = db.Column(db.String(20), nullable=False)  # "present" / "absent"
    heure = db.Column(db.String(5))       # exemple : "08:30"
    matiere = db.Column(db.String(120))   # exemple : "Math"

    eleve = db.relationship("Eleve", backref="presences")     
    



class PeriodeBulletin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(50), nullable=False)   # Exemple : "Trimestre 1", "Semestre 1"
    annee_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    publie = db.Column(db.Boolean, default=False)    # False = désactivé, True = activé
    date_publication = db.Column(db.DateTime)        # Quand admin clique sur "Publier"
    periode_active = db.Column(db.Boolean, default=False)  # Période actuellement active
    
    # Relations
    annee = db.relationship('AnneeScolaire', backref='periodes_bulletin')
    ecole = db.relationship('Ecole', backref='periodes_bulletin')

    # Timestamps
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Méthode utilitaire
    def est_active(self):
        return self.publie and self.periode_active
