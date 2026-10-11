#!/bin/sh
set -e

# Persistent layout on the mounted PVC (/data):
#   /data/instance/compliance.db   (SQLite DB — app expects it at /app/instance)
#   /data/uploads/...              (uploaded files; UPLOAD_FOLDER points here)
#   /data/backups/...              (BACKUP_FOLDER points here)
mkdir -p /data/instance /data/uploads /data/backups

# First-boot seed: copy bundled DB + uploads into the volume only if empty,
# so redeploys never clobber live data.
if [ ! -f /data/instance/compliance.db ]; then
  echo "[entrypoint] seeding initial database into /data/instance"
  cp -a /seed/instance/. /data/instance/ 2>/dev/null || true
fi
if [ ! -e /data/uploads/.seeded ]; then
  echo "[entrypoint] seeding initial uploads into /data/uploads"
  cp -a /seed/uploads/. /data/uploads/ 2>/dev/null || true
  touch /data/uploads/.seeded
fi

# The SQLite path is fixed to <app>/instance/compliance.db in config.py, so
# point /app/instance at the persistent volume.
rm -rf /app/instance
ln -snf /data/instance /app/instance

echo "[entrypoint] starting: $*"
exec "$@"
