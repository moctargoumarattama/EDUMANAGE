import importlib.util
from sqlalchemy import inspect, Integer, Float, Boolean, Date, DateTime
from sqlalchemy.orm import class_mapper, RelationshipProperty
from app import db, create_app
from app.models import Base, JournalCorrection, Ecole
from datetime import datetime
from flask import current_app

# Charger dynamiquement la fonction integrity_check depuis app/scripts.py
spec = importlib.util.spec_from_file_location("scripts", "app/scripts.py")
scripts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scripts)
integrity_check = getattr(scripts, "integrity_check")

def get_column_type_sql(model_class, column_name):
    """Retourne le type SQL exact pour SQLite à partir du modèle SQLAlchemy."""
    column = getattr(model_class, column_name, None)
    if not column:
        return "TEXT"
    col_type = column.property.columns[0].type
    if isinstance(col_type, Integer):
        return "INTEGER"
    elif isinstance(col_type, Float):
        return "REAL"
    elif isinstance(col_type, Boolean):
        return "INTEGER"
    elif isinstance(col_type, (Date, DateTime)):
        return "TEXT"
    else:
        return "TEXT"

def get_foreign_key_sql(model_class, column_name):
    """Retourne la clause FOREIGN KEY si la colonne est une relation."""
    mapper = class_mapper(model_class)
    for prop in mapper.iterate_properties:
        if hasattr(prop, 'columns'):
            for col in prop.columns:
                if col.name == column_name and col.foreign_keys:
                    fk = list(col.foreign_keys)[0]
                    return f"REFERENCES {fk.column.table.name}({fk.column.name})"
    return ""

def auto_fix_integrity_multi_ecoles():
    """Détecte et corrige les colonnes manquantes pour toutes les écoles."""
    results = integrity_check()
    inspector = inspect(db.engine)

    total_fixes = 0
    ecoles = Ecole.query.all()
    if not ecoles:
        current_app.logger.warning("⚠️ Aucune école trouvée pour auto-fix !")
        return

    for ecole in ecoles:
        fixes_par_ecole = 0
        current_app.logger.info(f"[AUTO-FIX] Vérification pour l'école : {ecole.nom}")

        for msg in results:
            if "Colonne manquante dans" in msg:
                try:
                    parts = msg.split(" ")
                    table_name = parts[3]
                    column_name = parts[-1]
                except Exception:
                    current_app.logger.warning(f"Impossible de parser le message: {msg}")
                    continue

                # Vérifie si colonne existe
                existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
                if column_name in existing_columns:
                    continue

                # Trouver le modèle correspondant
                model_class = None
                for cls in Base._decl_class_registry.values():
                    if hasattr(cls, "__tablename__") and cls.__tablename__ == table_name:
                        model_class = cls
                        break

                col_type = "TEXT"
                fk_sql = ""
                if model_class:
                    col_type = get_column_type_sql(model_class, column_name)
                    fk_sql = get_foreign_key_sql(model_class, column_name)

                sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {col_type}"
                if fk_sql:
                    sql += f" {fk_sql}"
                sql += ";"

                db.session.execute(sql)
                fixes_par_ecole += 1
                total_fixes += 1

                # Journalisation
                db.session.add(JournalCorrection(
                    action="AutoFix",
                    description=f"Ajout colonne {column_name} dans {table_name} ({col_type}) {fk_sql}",
                    date=datetime.utcnow(),
                    ecole_id=ecole.id
                ))
                current_app.logger.info(f"[AUTO-FIX][{ecole.nom}] {table_name}.{column_name} ajouté")

        # Gestion des tables many-to-many
        for cls in Base._decl_class_registry.values():
            if not hasattr(cls, "__table__"):
                continue
            mapper = class_mapper(cls)
            for prop in mapper.relationships:
                if isinstance(prop, RelationshipProperty) and prop.secondary is not None:
                    table = prop.secondary
                    for col in table.c:
                        existing_columns = [c['name'] for c in inspector.get_columns(table.name)]
                        if col.name not in existing_columns:
                            col_type = "INTEGER" if 'id' in col.name else "TEXT"
                            sql = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type};"
                            db.session.execute(sql)
                            fixes_par_ecole += 1
                            total_fixes += 1
                            db.session.add(JournalCorrection(
                                action="AutoFix",
                                description=f"Ajout colonne many-to-many {col.name} dans {table.name} ({col_type})",
                                date=datetime.utcnow(),
                                ecole_id=ecole.id
                            ))
                            current_app.logger.info(f"[AUTO-FIX][{ecole.nom}] {table.name}.{col.name} ajouté (many-to-many)")

        current_app.logger.info(f"[AUTO-FIX] {fixes_par_ecole} colonnes corrigées pour l'école {ecole.nom}")

    db.session.commit()
    current_app.logger.info(f"[AUTO-FIX] Total colonnes corrigées toutes écoles : {total_fixes}")

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        auto_fix_integrity_multi_ecoles()
