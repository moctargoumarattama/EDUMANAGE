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
    niveaux_config = db.relationship('AnneeNiveauConfig', back_populates='annee_scolaire', lazy=True, cascade="all, delete-orphan")

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
class NiveauScolaire(db.Model):
    __tablename__ = 'niveau_scolaire'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True)
    nom = db.Column(db.String(50), nullable=False)
    cycle = db.Column(db.String(20), nullable=False)
    ordre = db.Column(db.Integer, nullable=False, index=True)
    niveau_suivant_id = db.Column(db.Integer, db.ForeignKey('niveau_scolaire.id'), nullable=True)

    niveau_suivant = db.relationship('NiveauScolaire', remote_side=[id], lazy=True)
    configurations_ecoles = db.relationship('EcoleNiveauConfig', back_populates='niveau', lazy=True)
    configurations_annuelles = db.relationship('AnneeNiveauConfig', back_populates='niveau', lazy=True)
    classes = db.relationship('Classe', back_populates='niveau_scolaire', lazy=True)

    __table_args__ = (
        db.Index('ix_niveau_scolaire_cycle_ordre', 'cycle', 'ordre'),
    )

    def __repr__(self):
        return f'<NiveauScolaire {self.code}>'

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "nom": self.nom,
            "cycle": self.cycle,
            "ordre": self.ordre,
            "niveau_suivant_id": self.niveau_suivant_id,
        }


class EcoleNiveauConfig(db.Model):
    __tablename__ = 'ecole_niveau_config'

    id = db.Column(db.Integer, primary_key=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    niveau_id = db.Column(db.Integer, db.ForeignKey('niveau_scolaire.id'), nullable=False)
    actif = db.Column(db.Boolean, nullable=False, default=True)
    date_activation = db.Column(db.DateTime, nullable=True)
    date_desactivation = db.Column(db.DateTime, nullable=True)

    ecole = db.relationship('Ecole', back_populates='niveaux_config')
    niveau = db.relationship('NiveauScolaire', back_populates='configurations_ecoles')

    __table_args__ = (
        db.UniqueConstraint('ecole_id', 'niveau_id', name='uq_ecole_niveau_config'),
        db.Index('ix_ecole_niveau_config_ecole_actif', 'ecole_id', 'actif'),
    )

    def __repr__(self):
        return f'<EcoleNiveauConfig ecole={self.ecole_id} niveau={self.niveau_id} actif={self.actif}>'


class AnneeNiveauConfig(db.Model):
    __tablename__ = 'annee_niveau_config'

    id = db.Column(db.Integer, primary_key=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False)
    niveau_id = db.Column(db.Integer, db.ForeignKey('niveau_scolaire.id'), nullable=False)
    actif = db.Column(db.Boolean, nullable=False, default=True)

    ecole = db.relationship('Ecole', back_populates='niveaux_annuels_config')
    annee_scolaire = db.relationship('AnneeScolaire', back_populates='niveaux_config')
    niveau = db.relationship('NiveauScolaire', back_populates='configurations_annuelles')

    __table_args__ = (
        db.UniqueConstraint('ecole_id', 'annee_scolaire_id', 'niveau_id', name='uq_annee_niveau_config'),
        db.Index('ix_annee_niveau_config_annee_actif', 'annee_scolaire_id', 'actif'),
        db.Index('ix_annee_niveau_config_ecole_annee', 'ecole_id', 'annee_scolaire_id'),
    )

    def __repr__(self):
        return f'<AnneeNiveauConfig ecole={self.ecole_id} annee={self.annee_scolaire_id} niveau={self.niveau_id} actif={self.actif}>'


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
    motif_blocage = db.Column(db.String(300))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    logo_path = db.Column(db.String(200))  # <-- champ existant
    signature_path = db.Column(db.String(200))
    cachet_path = db.Column(db.String(200))
    ville = db.Column(db.String(100))

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
    niveaux_config = db.relationship('EcoleNiveauConfig', back_populates='ecole', lazy=True, cascade="all, delete-orphan")
    niveaux_annuels_config = db.relationship('AnneeNiveauConfig', back_populates='ecole', lazy=True, cascade="all, delete-orphan")
    google_mail_config = db.relationship(
        'EcoleGoogleMailConfig',
        back_populates='ecole',
        uselist=False,
        cascade='all, delete-orphan',
        passive_deletes=True
    )

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
            "motif_blocage": self.motif_blocage,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "logo_path": self.logo_path,
            "signature_path": getattr(self, 'signature_path', None),
            "cachet_path": getattr(self, 'cachet_path', None),
            "ville": getattr(self, 'ville', None)
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
    admin_tour_version = db.Column(db.Integer, nullable=False, default=0, server_default='0')

    # nullable=True pour permettre aux super admins de ne pas avoir d'école
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='SET NULL'), nullable=True)
    ecole = db.relationship('Ecole', back_populates='utilisateurs')

    __table_args__ = (
        db.Index('ix_utilisateur_ecole_role', 'ecole_id', 'role'),
    )

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
        return self.professeur_rel if self.role == 'professeur' else None

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
            "admin_tour_version": self.admin_tour_version,
            "ecole_id": self.ecole_id
        }

ADMIN_TOUR_VERSION = 1


@event.listens_for(Utilisateur, 'before_delete')
def protect_super_admin_delete(mapper, connection, target):
    """Protection de sécurité critique : empêche toute suppression du super_admin"""
    if target.role == 'super_admin':
        raise ValueError("Protection critique : le compte Super Administrateur ne peut jamais être supprimé.")

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

    __table_args__ = (
        db.Index('ix_professeur_utilisateur_ecole', 'utilisateur_id', 'ecole_id'),
    )

    utilisateur = db.relationship('Utilisateur', back_populates='professeur_rel', uselist=False)
    cours = db.relationship('Cours', back_populates='professeur', lazy=True, cascade="all, delete-orphan")
    emplois_du_temps = db.relationship('EmploiTemps', back_populates='professeur', lazy=True, cascade="all, delete-orphan")

    classes_assignees = db.relationship(
        'Classe',
        secondary=professeur_classes,
        back_populates='professeurs_assignes',
        lazy='dynamic'
    )

    @property
    def classes(self):
        return self.classes_assignees.all()

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
    niveau_id = db.Column(db.Integer, db.ForeignKey('niveau_scolaire.id'), nullable=True)
    section = db.Column(db.String(30), nullable=True)
    effectif = db.Column(db.Integer, default=0)
    capacite = db.Column(db.Integer, default=30)
    statut = db.Column(db.String(20), nullable=False, default='ouverte', server_default='ouverte')

    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id'), nullable=False)
    ecole = db.relationship('Ecole', back_populates='classes')
    salle = db.Column(db.String(50))
    professeur_id = db.Column(db.Integer, db.ForeignKey("professeur.id"))
    professeur = db.relationship('Professeur', foreign_keys=[professeur_id], backref=db.backref('classes_dirigees', lazy=True))

    # Lien avec l'année scolaire
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False, default=1)
    annee_scolaire = db.relationship('AnneeScolaire', back_populates='classes')
    niveau_scolaire = db.relationship('NiveauScolaire', back_populates='classes')

    emplois = db.relationship('EmploiTemps', back_populates='classe', lazy=True, cascade="all, delete-orphan")
    cours = db.relationship('Cours', back_populates='classe', lazy=True, cascade="all, delete-orphan")
    capacite_max = db.Column(db.Integer, nullable=False, default=30)

    __table_args__ = (
        db.Index('ix_classe_ecole_annee', 'ecole_id', 'annee_scolaire_id'),
        db.Index('ix_classe_niveau_id', 'niveau_id'),
        db.UniqueConstraint('ecole_id', 'annee_scolaire_id', 'nom', name='uq_classe_ecole_annee_nom'),
    )

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
        if getattr(self, '_effectif_annuel', None) is not None:
            return self._effectif_annuel
        if not self.id:
            return 0
        if 'inscriptions' in self.__dict__:
            return sum(
                1 for ins in self.inscriptions
                if getattr(ins, 'ecole_id', None) == self.ecole_id
                and getattr(ins, 'annee_scolaire_id', None) == self.annee_scolaire_id
                and (getattr(ins, 'statut', 'inscrit') or 'inscrit') != 'desinscrit'
            )
        return 0

    @property
    def capacite_totale(self):
        return self.capacite or self.capacite_max or 35

    @property
    def est_pleine(self):
        return self.effectif_reel >= self.capacite_totale

    @property
    def est_ouverte(self):
        return (self.statut or 'ouverte') == 'ouverte'

    def __repr__(self):
        return f'<Classe {self.nom_complet}>'

    def to_dict(self):
        eff = self.effectif_reel
        return {
            "id": self.id,
            "nom": self.nom,
            "nom_complet": self.nom_complet,
            "niveau": self.niveau,
            "effectif": eff,
            "ecole_id": self.ecole_id,
            "salle": self.salle,
            "professeur_id": self.professeur_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "statut": self.statut,
            "est_ouverte": self.est_ouverte,
            "capacite": self.capacite_totale,
            "capacite_max": self.capacite_totale,
            "effectif_reel": eff,
            # Professeurs assignés (structure légère pour éviter récursion circulaire)
            "professeurs_assignes": [
                {"id": prof.id, "nom": prof.nom, "prenom": prof.prenom, "specialite": prof.specialite}
                for prof in self.professeurs_assignes
            ]
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

    # parent_id : ondelete SET NULL pour ne pas supprimer un élève si le parent est supprimé
    parent_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id', ondelete='SET NULL'), nullable=True)
    parent = db.relationship('Utilisateur', back_populates='enfants', foreign_keys=[parent_id])

    notes = db.relationship('Note', backref='eleve', lazy=True, cascade="all, delete-orphan")
    paiements = db.relationship('Paiement', backref='eleve', lazy=True, cascade="all, delete-orphan")
    absences = db.relationship('Absence', backref='eleve', lazy=True, cascade="all, delete-orphan")
    alertes = db.relationship('Alerte', back_populates='eleve', lazy=True)
    annee_premiere_ecole = db.Column(db.Integer)

    __table_args__ = (
        db.Index('ix_eleve_parent_id', 'parent_id'),
    )

    @staticmethod
    def generer_code_parent(length=8):
        lettres_chiffres = string.ascii_uppercase + string.digits
        while True:
            code = ''.join(random.choices(lettres_chiffres, k=length))
            if not Eleve.query.filter_by(code_parent=code).first():
                return code

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
    inscription_id = db.Column(
        db.Integer,
        db.ForeignKey('inscriptions.id', ondelete='RESTRICT'),
        nullable=True,
        index=True
    )

    sync_version = db.Column(db.Integer, default=1, nullable=False, server_default='1')
    last_by_admin = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_note_ecole_eleve', 'ecole_id', 'eleve_id'),
        db.Index('ix_note_cours_eleve', 'cours_id', 'eleve_id'),
    )

    inscription = db.relationship('Inscription', backref=db.backref('notes', lazy=True))

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
            "ecole_id": self.ecole_id,
            "inscription_id": self.inscription_id,
            "sync_version": self.sync_version or 1,
            "last_by_admin": bool(self.last_by_admin),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
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
    inscription_id = db.Column(
        db.Integer,
        db.ForeignKey('inscriptions.id', ondelete='RESTRICT'),
        nullable=True,
        index=True
    )

    __table_args__ = (
        db.Index('ix_paiement_ecole_statut', 'ecole_id', 'statut'),
    )

    inscription = db.relationship('Inscription', backref=db.backref('paiements', lazy=True))

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
            "ecole_id": self.ecole_id,
            "inscription_id": self.inscription_id
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
    inscription_id = db.Column(
        db.Integer,
        db.ForeignKey('inscriptions.id', ondelete='RESTRICT'),
        nullable=True,
        index=True
    )

    sync_version = db.Column(db.Integer, default=1, nullable=False, server_default='1')
    last_by_admin = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_absence_ecole_eleve', 'ecole_id', 'eleve_id'),
    )

    inscription = db.relationship('Inscription', backref=db.backref('absences', lazy=True))

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
            "ecole_id": self.ecole_id,
            "inscription_id": self.inscription_id,
            "sync_version": self.sync_version or 1,
            "last_by_admin": bool(self.last_by_admin),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
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

    __table_args__ = (
        db.Index('ix_cours_ecole_classe', 'ecole_id', 'classe_id'),
    )

    # Relations existantes
    classe = db.relationship('Classe', back_populates='cours')
    professeur = db.relationship('Professeur', back_populates='cours')
    notes = db.relationship('Note', backref='cours', lazy=True, cascade="all, delete-orphan")
    absences = db.relationship('Absence', backref='cours', lazy=True, cascade="all, delete-orphan")
    emplois_du_temps = db.relationship('EmploiTemps', back_populates='cours', lazy=True, cascade="all, delete-orphan")

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
            "professeur_id": self.professeur_id
        }

# -----------------------
# Bulletin
# -----------------------
class Bulletin(db.Model):
    __tablename__ = 'bulletin'

    id = db.Column(db.Integer, primary_key=True)

    # Ancrage annuel central (source de vérité)
    inscription_id = db.Column(
        db.Integer,
        db.ForeignKey('inscriptions.id', ondelete='RESTRICT'),
        nullable=True,
        index=True
    )

    # Liaisons d'intégrité et de compatibilité
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleve.id', ondelete='CASCADE'), nullable=False, index=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False, index=True)
    classe_id = db.Column(db.Integer, db.ForeignKey('classe.id', ondelete='RESTRICT'), nullable=True, index=True)
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id', ondelete='RESTRICT'), nullable=True, index=True)

    # Données académiques du bulletin
    periode = db.Column(db.String(50), nullable=True, default="Trimestre 1")
    moyenne_generale = db.Column(db.Float, nullable=True)
    rang = db.Column(db.Integer, nullable=True)
    rang_total = db.Column(db.Integer, nullable=True)
    appreciation_generale = db.Column(db.String(255), nullable=True)
    statut = db.Column(db.String(20), nullable=False, default='valide')

    # Champs historiques / rétrocompatibilité
    matiere = db.Column(db.String(100), nullable=True)
    note = db.Column(db.Float, nullable=True)
    annee = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations ORM
    inscription = db.relationship('Inscription', backref=db.backref('bulletins', lazy=True))
    eleve = db.relationship('Eleve', backref=db.backref('bulletins', lazy=True))
    ecole = db.relationship('Ecole', backref=db.backref('bulletins', lazy=True))
    classe = db.relationship('Classe', backref=db.backref('bulletins', lazy=True))
    annee_scolaire = db.relationship('AnneeScolaire', backref=db.backref('bulletins', lazy=True))

    __table_args__ = (
        db.Index('ix_bulletin_inscription_periode', 'inscription_id', 'periode'),
        db.Index('ix_bulletin_ecole_annee', 'ecole_id', 'annee_scolaire_id'),
    )

    def __repr__(self):
        return f'<Bulletin {self.id} - Inscription {self.inscription_id} - {self.periode}>'

    def to_dict(self):
        return {
            "id": self.id,
            "inscription_id": self.inscription_id,
            "eleve_id": self.eleve_id,
            "ecole_id": self.ecole_id,
            "classe_id": self.classe_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "periode": self.periode,
            "moyenne_generale": self.moyenne_generale,
            "rang": self.rang,
            "rang_total": self.rang_total,
            "appreciation_generale": self.appreciation_generale,
            "statut": self.statut,
            "matiere": self.matiere,
            "note": self.note,
            "annee": self.annee,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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

    @property
    def annee_scolaire(self):
        return self.classe.annee_scolaire if self.classe else None

    @property
    def annee_scolaire_id(self):
        return self.classe.annee_scolaire_id if self.classe else None

    def __repr__(self):
        return f'<EmploiTemps {self.jour} {self.heure_debut}-{self.heure_fin}>'

    def to_dict(self):
        return {
            "id": self.id,
            "professeur_id": self.professeur_id,
            "professeur_nom": f"{self.professeur.prenom} {self.professeur.nom}" if self.professeur else None,
            "jour": self.jour,
            "heure_debut": self.heure_debut.isoformat() if self.heure_debut else None,
            "heure_fin": self.heure_fin.isoformat() if self.heure_fin else None,
            "cours_id": self.cours_id,
            "cours_nom": self.cours.nom if self.cours else None,
            "classe_id": self.classe_id,
            "classe_nom": self.classe.nom if self.classe else None,
            "annee_scolaire_id": self.annee_scolaire_id,
            "salle": self.salle,
            "ecole_id": self.ecole_id,
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
    ecole_id = db.Column(db.Integer, db.ForeignKey("ecole.id"), nullable=False)
    eleve_id = db.Column(db.Integer, db.ForeignKey("eleve.id"), nullable=False)
    classe_id = db.Column(db.Integer, db.ForeignKey("classe.id"), nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey("cours.id"), nullable=True)

    # REMPLACER annee_scolaire par annee_scolaire_id
    annee_scolaire_id = db.Column(db.Integer, db.ForeignKey('annee_scolaire.id'), nullable=False)
    annee_scolaire = db.relationship('AnneeScolaire', back_populates='inscriptions')

    statut = db.Column(db.String(20), nullable=False, default='inscrit')
    frais_annuels = db.Column(db.Float, nullable=True)
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    date_sortie = db.Column(db.DateTime, nullable=True)
    motif_sortie = db.Column(db.String(255), nullable=True)
    decision_fin_annee = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    ecole = db.relationship("Ecole", backref="inscriptions")
    eleve = db.relationship("Eleve", backref="inscriptions", foreign_keys=[eleve_id])
    classe = db.relationship("Classe", backref="inscriptions", foreign_keys=[classe_id])
    cours = db.relationship("Cours", backref="inscriptions", foreign_keys=[cours_id])

    __table_args__ = (
        db.UniqueConstraint('ecole_id', 'annee_scolaire_id', 'eleve_id', name='uq_inscription_ecole_annee_eleve'),
        db.Index('ix_inscription_ecole_annee', 'ecole_id', 'annee_scolaire_id'),
        db.Index('ix_inscription_eleve_annee', 'eleve_id', 'annee_scolaire_id'),
        db.Index('ix_inscription_classe', 'classe_id'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "ecole_id": self.ecole_id,
            "eleve_id": self.eleve_id,
            "classe_id": self.classe_id,
            "cours_id": self.cours_id,
            "annee_scolaire_id": self.annee_scolaire_id,
            "statut": self.statut,
            "frais_annuels": self.frais_annuels,
            "date_inscription": self.date_inscription.isoformat() if self.date_inscription else None,
            "date_sortie": self.date_sortie.isoformat() if self.date_sortie else None,
            "motif_sortie": self.motif_sortie,
            "decision_fin_annee": self.decision_fin_annee
        }

@event.listens_for(Eleve, "after_insert")
def creer_inscription(mapper, connection, target):
    # Phase 2A: inscriptions are created by the central service.
    return

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
    date_debut = db.Column(db.Date, nullable=True)
    date_fin = db.Column(db.Date, nullable=True)
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


# -----------------------------------------------------------------------------
# Configuration Gmail par École (Google OAuth 2.0 & Gmail API)
# -----------------------------------------------------------------------------
class EcoleGoogleMailConfig(db.Model):
    __tablename__ = 'ecole_google_mail_config'

    id = db.Column(db.Integer, primary_key=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    google_email = db.Column(db.String(120), nullable=True)
    google_refresh_token_encrypted = db.Column(db.Text, nullable=True)
    google_access_token_encrypted = db.Column(db.Text, nullable=True)
    token_expiry = db.Column(db.DateTime, nullable=True)
    is_connected = db.Column(db.Boolean, default=False, nullable=False)
    connected_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    ecole = db.relationship('Ecole', back_populates='google_mail_config')

    def __repr__(self):
        return f"<EcoleGoogleMailConfig ecole_id={self.ecole_id} email={self.google_email} connected={self.is_connected}>"

    def to_dict(self):
        return {
            "id": self.id,
            "ecole_id": self.ecole_id,
            "google_email": self.google_email,
            "is_connected": self.is_connected,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# -----------------------------------------------------------------------------
# Journal d'idempotence et d'audit pour la synchronisation hors-ligne
# -----------------------------------------------------------------------------
class SyncOperationLog(db.Model):
    __tablename__ = 'sync_operation_log'

    id = db.Column(db.Integer, primary_key=True)
    client_op_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id', ondelete='CASCADE'), nullable=False, index=True)
    entity_type = db.Column(db.String(32), nullable=False)  # 'note', 'absence', 'paiement'
    entity_id = db.Column(db.Integer, nullable=True)        # ID de l'entité créée/mise à jour
    status = db.Column(db.String(32), nullable=False, default='synced')  # 'synced', 'already_processed', 'conflict', 'forbidden', 'error'
    payload_hash = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relations
    ecole = db.relationship('Ecole', backref=db.backref('sync_logs', lazy='dynamic'))
    utilisateur = db.relationship('Utilisateur', backref=db.backref('sync_logs', lazy='dynamic'))

    @property
    def user(self):
        return self.utilisateur

    def __repr__(self):
        return f"<SyncOperationLog op_id={self.client_op_id} type={self.entity_type} status={self.status}>"

    def to_dict(self):
        return {
            "id": self.id,
            "client_op_id": self.client_op_id,
            "ecole_id": self.ecole_id,
            "user_id": self.user_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "status": self.status,
            "payload_hash": self.payload_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# -----------------------------------------------------------------------------
# Modèle Support KLASORA (Tickets de support technique plateforme)
# -----------------------------------------------------------------------------
class SupportTicket(db.Model):
    __tablename__ = 'support_ticket'

    id = db.Column(db.Integer, primary_key=True)
    ecole_id = db.Column(db.Integer, db.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False, index=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(32), nullable=False)
    sujet = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    page_url = db.Column(db.String(255), nullable=True)
    statut = db.Column(db.String(20), nullable=False, default='nouveau', index=True)  # 'nouveau', 'en_cours', 'resolu'
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)

    # Relations
    ecole = db.relationship('Ecole', backref=db.backref('support_tickets', lazy='dynamic', cascade='all, delete-orphan'))
    utilisateur = db.relationship('Utilisateur', backref=db.backref('support_tickets', lazy='dynamic', cascade='all, delete-orphan'))

    def __repr__(self):
        return f"<SupportTicket id={self.id} ecole_id={self.ecole_id} statut={self.statut} sujet={self.sujet[:30]}>"

    def to_dict(self):
        return {
            "id": self.id,
            "ecole_id": self.ecole_id,
            "ecole_nom": self.ecole.nom if self.ecole else None,
            "utilisateur_id": self.utilisateur_id,
            "utilisateur_nom": f"{self.utilisateur.prenom} {self.utilisateur.nom}" if self.utilisateur else None,
            "utilisateur_email": self.utilisateur.email if self.utilisateur else None,
            "role": self.role,
            "sujet": self.sujet,
            "message": self.message,
            "page_url": self.page_url,
            "statut": self.statut,
            "user_agent": self.user_agent,
            "created_at": self.created_at.strftime('%d/%m/%Y %H:%M') if self.created_at else None,
            "updated_at": self.updated_at.strftime('%d/%m/%Y %H:%M') if self.updated_at else None,
            "resolved_at": self.resolved_at.strftime('%d/%m/%Y %H:%M') if self.resolved_at else None,
        }


# -----------------------------------------------------------------------------
# Modèle Demandes de Présentation KLASORA (Demandes de contact vitrine publique)
# -----------------------------------------------------------------------------
class DemandePresentation(db.Model):
    __tablename__ = 'demande_presentation'

    id = db.Column(db.Integer, primary_key=True)
    nom_ecole = db.Column(db.String(150), nullable=False)
    telephone = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    ville = db.Column(db.String(100), nullable=True)
    message = db.Column(db.Text, nullable=True)
    statut = db.Column(db.String(30), nullable=False, default='nouvelle', index=True)  # 'nouvelle', 'contactee', 'traitee', 'archivee'
    notes_admin = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<DemandePresentation id={self.id} ecole='{self.nom_ecole}' statut='{self.statut}'>"

    def to_dict(self):
        return {
            "id": self.id,
            "nom_ecole": self.nom_ecole,
            "telephone": self.telephone,
            "email": self.email,
            "ville": self.ville,
            "message": self.message,
            "statut": self.statut,
            "notes_admin": self.notes_admin,
            "created_at": self.created_at.strftime("%d/%m/%Y %H:%M") if self.created_at else None
        }


