"""Migration idempotente des matricules, sans démarrer Flask.

Prévisualisation : python scripts/migrate_matricules_format.py
Application     : python scripts/migrate_matricules_format.py --apply
La base SQLite est sauvegardée avant application. PostgreSQL utilise la même
transaction pour le schéma et les données. Seule eleve.matricule est modifiée.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import re
import sqlite3
import sys

import sqlalchemy as sa


PATTERN = re.compile(r"^[0-9]{2}-[0-9]{4}$")
ROOT = Path(__file__).resolve().parents[1]


def _conforme(value):
    return isinstance(value, str) and bool(PATTERN.fullmatch(value)) and value[-4:] != "0000"


def _date(value):
    if isinstance(value, (date, datetime)):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return None


def _sequences(metadata):
    return sa.Table(
        'matricule_sequence', metadata,
        sa.Column('ecole_id', sa.Integer, sa.ForeignKey('ecole.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('prefixe', sa.String(2), primary_key=True),
        sa.Column('dernier_numero', sa.Integer, nullable=False),
        sa.CheckConstraint('dernier_numero >= 0 AND dernier_numero <= 9999', name='ck_matricule_sequence_capacite'),
    )


def preparer_migration(connection):
    """Plan complet validé avant la première modification ; aucun nom/PIN exporté."""
    inspector = sa.inspect(connection)
    if not inspector.has_table('eleve'):
        raise ValueError("La base ne contient pas la table eleve.")
    metadata = sa.MetaData()
    eleves = sa.Table('eleve', metadata, autoload_with=connection)
    rows = list(connection.execute(sa.select(eleves)).mappings())
    premieres_annees = {}
    if inspector.has_table('inscriptions') and inspector.has_table('annee_scolaire'):
        inscriptions = sa.Table('inscriptions', metadata, autoload_with=connection)
        annees = sa.Table('annee_scolaire', metadata, autoload_with=connection)
        for eleve_id, date_debut in connection.execute(
            sa.select(inscriptions.c.eleve_id, sa.func.min(annees.c.date_debut))
            .join(annees, inscriptions.c.annee_scolaire_id == annees.c.id)
            .group_by(inscriptions.c.eleve_id)
        ):
            if _date(date_debut):
                premieres_annees[eleve_id] = _date(date_debut).year

    compteurs = defaultdict(int)
    deja_attribues = set()
    for row in rows:
        value = row.get('matricule')
        if _conforme(value):
            key = (row['ecole_id'], value)
            if key in deja_attribues:
                raise ValueError("Deux élèves de la même école ont déjà le même matricule conforme. Migration annulée.")
            deja_attribues.add(key)
            counter_key = (row['ecole_id'], value[:2])
            compteurs[counter_key] = max(compteurs[counter_key], int(value[-4:]))
    if inspector.has_table('matricule_sequence'):
        sequences = sa.Table('matricule_sequence', metadata, autoload_with=connection)
        for row in connection.execute(sa.select(sequences)).mappings():
            key = (row['ecole_id'], row['prefixe'])
            compteurs[key] = max(compteurs[key], row['dernier_numero'])

    modifications = []
    for row in sorted(rows, key=lambda r: (
        str(r.get('date_inscription') or r.get('created_at') or ''), r['id']
    )):
        if _conforme(row.get('matricule')):
            continue
        created = _date(row.get('created_at')) or _date(row.get('date_inscription'))
        annee = premieres_annees.get(row['id']) or row.get('annee_premiere_ecole') or (created.year if created else 2024)
        annee = int(annee)
        if not 1000 <= annee <= 9999:
            raise ValueError("Une année d'entrée invalide empêche la migration.")
        prefixe = f"{annee % 100:02d}"
        key = (row['ecole_id'], prefixe)
        compteurs[key] += 1
        if compteurs[key] > 9999:
            raise ValueError(f"Plus de 9 999 matricules sont nécessaires pour l'école {row['ecole_id']} et le préfixe {prefixe}. Migration annulée.")
        modifications.append({'id': row['id'], 'matricule': f"{prefixe}-{compteurs[key]:04d}"})
    return rows, modifications, compteurs


def _proteger_permanence(connection):
    if connection.dialect.name == 'sqlite':
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_eleve_matricule_permanent
            BEFORE UPDATE OF matricule ON eleve
            WHEN OLD.matricule GLOB '[0-9][0-9]-[0-9][0-9][0-9][0-9]'
              AND substr(OLD.matricule, 4, 4) != '0000'
              AND OLD.matricule IS NOT NEW.matricule
            BEGIN
                SELECT RAISE(ABORT, 'Le matricule scolaire est permanent');
            END
        """)
    elif connection.dialect.name == 'postgresql':
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION klasora_proteger_matricule() RETURNS trigger AS $$
            BEGIN
                IF OLD.matricule ~ '^[0-9]{2}-[0-9]{4}$'
                   AND right(OLD.matricule, 4) <> '0000'
                   AND OLD.matricule IS DISTINCT FROM NEW.matricule THEN
                    RAISE EXCEPTION 'Le matricule scolaire est permanent';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        connection.exec_driver_sql('DROP TRIGGER IF EXISTS trg_eleve_matricule_permanent ON eleve')
        connection.exec_driver_sql("""
            CREATE TRIGGER trg_eleve_matricule_permanent BEFORE UPDATE OF matricule
            ON eleve FOR EACH ROW EXECUTE FUNCTION klasora_proteger_matricule()
        """)


def migrate_matricules(connection, dry_run=False):
    """Fonction partagée CLI/Alembic ; l'appelant contrôle commit/rollback."""
    if not dry_run:
        if connection.dialect.name == 'sqlite':
            # Le driver legacy n'ouvre pas de transaction pour SELECT/ALTER TABLE.
            # Assurer l'atomicité aussi lorsque l'appel vient d'Alembic.
            driver = connection.connection.driver_connection
            if not driver.in_transaction:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
        elif connection.dialect.name == 'postgresql':
            # Même ordre de verrous que la création : école, puis élèves/séquence.
            connection.exec_driver_sql('SELECT id FROM ecole ORDER BY id FOR UPDATE').all()
            connection.exec_driver_sql('LOCK TABLE eleve IN SHARE ROW EXCLUSIVE MODE')
    rows, modifications, compteurs = preparer_migration(connection)
    result = {'eleves': len(rows), 'a_migrer': len(modifications), 'conformes': len(rows) - len(modifications), 'dry_run': dry_run}
    if dry_run:
        return result
    inspector = sa.inspect(connection)
    if 'matricule' not in {c['name'] for c in inspector.get_columns('eleve')}:
        connection.exec_driver_sql('ALTER TABLE eleve ADD COLUMN matricule VARCHAR(12)')
    # SQL Core évite updated_at/onupdate : les autres données restent identiques.
    for change in modifications:
        connection.execute(sa.text('UPDATE eleve SET matricule = :matricule WHERE id = :id'), change)
    connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS uq_eleve_ecole_matricule ON eleve (ecole_id, matricule)')
    connection.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_eleve_matricule ON eleve (matricule)')
    metadata = sa.MetaData()
    sa.Table('ecole', metadata, autoload_with=connection)
    if inspector.has_table('matricule_sequence'):
        sequences = sa.Table('matricule_sequence', metadata, autoload_with=connection)
    else:
        sequences = _sequences(metadata)
        sequences.create(connection)
    for (ecole_id, prefixe), numero in compteurs.items():
        condition = sa.and_(sequences.c.ecole_id == ecole_id, sequences.c.prefixe == prefixe)
        existing = connection.execute(sa.select(sequences.c.dernier_numero).where(condition)).scalar_one_or_none()
        if existing is None:
            connection.execute(sequences.insert().values(ecole_id=ecole_id, prefixe=prefixe, dernier_numero=numero))
        elif existing < numero:
            connection.execute(sequences.update().where(condition).values(dernier_numero=numero))
    values = connection.execute(sa.text('SELECT matricule FROM eleve')).scalars().all()
    if len(values) != len(rows) or not all(_conforme(value) for value in values):
        raise ValueError('Validation finale des matricules échouée. Migration annulée.')
    _proteger_permanence(connection)
    result.update(conformes=len(values), migres=len(modifications))
    return result


def _database_url(explicit):
    from dotenv import load_dotenv
    import os

    load_dotenv(ROOT / '.env')
    url = explicit or os.environ.get('DATABASE_URL', '').strip()
    if url:
        if url.startswith('postgres://'):
            url = 'postgresql+psycopg://' + url[len('postgres://'):]
        elif url.startswith('postgresql://'):
            url = 'postgresql+psycopg://' + url[len('postgresql://'):]
        return sa.engine.make_url(url)
    return sa.engine.URL.create('sqlite', database=str(ROOT / 'instance' / 'ecole.db'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', help='Base cible (défaut : DATABASE_URL ou instance/ecole.db).')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Appliquer après sauvegarde SQLite.')
    mode.add_argument('--dry-run', action='store_true', help='Prévisualiser uniquement (défaut).')
    args = parser.parse_args(argv)
    url = _database_url(args.database_url)
    backup_path = None
    if url.get_backend_name() == 'sqlite':
        target = Path(url.database).resolve()
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('La base SQLite cible est absente ou vide.')
        if args.apply:
            backup_dir = ROOT / 'backups'
            backup_dir.mkdir(exist_ok=True)
            backup_path = backup_dir / f"matricules_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.db"
            with closing(sqlite3.connect(f'{target.as_uri()}?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
        else:
            url = url.set(database=f'file:{target.as_posix()}', query={'mode': 'ro', 'uri': 'true'})
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            if args.apply and connection.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            elif not args.apply and connection.dialect.name == 'sqlite':
                connection.exec_driver_sql('PRAGMA query_only=ON')
            result = migrate_matricules(connection, dry_run=not args.apply)
            if args.apply:
                connection.commit()
            else:
                connection.rollback()
        if backup_path:
            result['sauvegarde'] = str(backup_path)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        engine.dispose()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # Ne jamais afficher une URL avec identifiants ni les paramètres SQL.
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Migration annulée : {reason}. Aucune transaction partielle n'est conservée.", file=sys.stderr)
        sys.exit(1)
