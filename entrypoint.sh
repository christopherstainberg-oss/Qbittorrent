#!/bin/sh
# Seed a default config on first run so a fresh named volume "just works":
# the user can then configure via env vars and the web UI.
set -e

: "${CONFIG:=/config/config.yaml}"

if [ ! -f "$CONFIG" ]; then
  mkdir -p "$(dirname "$CONFIG")"
  cp /app/config.example.yaml "$CONFIG"
  echo "[entrypoint] Seeded default config at $CONFIG"
  echo "[entrypoint] Set QBIT_HOST/QBIT_USERNAME/QBIT_PASSWORD (and SONARR_*/RADARR_*)"
  echo "[entrypoint] as env vars, or edit the file / use the web UI."
fi

exec "$@"
