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

Le backup PostgreSQL global avec `pg_dump` / `pg_restore` sera traite en Phase
PostgreSQL 2. Le backup SQLite existant ne doit pas etre casse.
