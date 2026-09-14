# KLASORA - PostgreSQL Production

## Local

Le developpement local reste sur SQLite.

```env
DATABASE_URL=sqlite:///ecole.db
```

La base SQLite locale est jetable :

```bash
flask db upgrade
flask init-system
```

## Production

La production VPS utilise PostgreSQL via `DATABASE_URL`.

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg://klasora_app:***@127.0.0.1:5432/klasora
```

Ne pas exposer PostgreSQL sur Internet et ne pas stocker les secrets dans Git.

## Tests PostgreSQL

Les tests d'integration PostgreSQL utilisent une variable separee :

```env
TEST_POSTGRES_DATABASE_URL=postgresql+psycopg://klasora_test:***@127.0.0.1:5432/klasora_test
```

Sans cette variable, ils sont ignores.

## Python

Production recommandee : Python 3.11 ou 3.12 tant que la compatibilite complete
avec Python 3.14 des versions figees de numpy, pandas, Pillow et reportlab n'a
pas ete validee.

## Backup

### SQLite local

Le backup global SQLite produit un fichier `.db` dans `backups/`.

### PostgreSQL production

Le backup global PostgreSQL produit un dump custom `pg_dump` :

```bash
pg_dump --format=custom --no-owner --no-privileges --file backups/klasora-postgresql-YYYYMMDD-HHMMSS.dump
```

L'application ne met pas le mot de passe PostgreSQL dans la ligne de commande.
Les credentials viennent de `DATABASE_URL` et le mot de passe est transmis via
un environnement temporaire `PGPASSWORD`.

Variables optionnelles :

```env
BACKUP_MIN_FREE_MB=512
POSTGRES_BACKUP_RETENTION=7
BACKUP_RETENTION_DAYS=7
```

### Restore PostgreSQL

La restauration globale PostgreSQL utilise `pg_restore` et cree d'abord un
backup de securite. En production, elle doit etre lancee pendant une fenetre de
maintenance.

```bash
pg_restore --no-owner --no-privileges --clean --if-exists --dbname klasora backups/klasora-postgresql-YYYYMMDD-HHMMSS.dump
```

Ne jamais copier les fichiers internes PostgreSQL et ne jamais manipuler
directement `/var/lib/postgresql`.

### Backup par ecole

Le backup par ecole reste applicatif et filtre strictement par `ecole_id`. Il
produit un JSON avec checksum, utilisable avec SQLite et PostgreSQL.
