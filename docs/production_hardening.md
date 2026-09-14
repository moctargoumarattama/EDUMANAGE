# KLASORA - Production Hardening

## Variables

Les secrets doivent rester dans l'environnement serveur, jamais dans Git.

- `APP_ENV=production`
- `SECRET_KEY`: valeur longue et aleatoire.
- `SECURITY_PASSWORD_SALT`: valeur longue et aleatoire.
- `SUPERADMIN_PASSWORD`: obligatoire en production pour l'initialisation technique.
- `DATABASE_URL`: URL de la base applicative.
- `REDIS_URL`: optionnel, active le stockage Redis pour le rate limiting/cache.
- `MAX_CONTENT_LENGTH`: limite d'upload, 20 Mo par defaut.

## Sauvegarde planifiee

Commande applicative :

```bash
flask backup-schools-daily
```

Exemple cron quotidien :

```cron
15 2 * * * cd /opt/klasora && . .venv/bin/activate && flask backup-schools-daily >> logs/backup-cron.log 2>&1
```

Exemple systemd service :

```ini
[Unit]
Description=Klasora daily backup

[Service]
Type=oneshot
WorkingDirectory=/opt/klasora
Environment=APP_ENV=production
ExecStart=/opt/klasora/.venv/bin/flask backup-schools-daily
```

## Verification runtime

Commande read-only :

```bash
flask system-health
```

Elle affiche l'espace disque libre et les tailles approximatives de la base SQLite, des sauvegardes et des uploads.

Endpoint HTTP :

```text
GET /health
```

Retourne `200` si la base repond a `SELECT 1`, sinon `503`.

## Rotation des secrets

1. Generer une nouvelle valeur forte.
2. Mettre a jour les variables d'environnement sur le serveur.
3. Redemarrer l'application.
4. Verifier `/health` et une connexion admin.

Ne pas stocker les anciennes valeurs dans le depot.
