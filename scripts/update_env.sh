#!/usr/bin/env bash

# Secure an existing environment file without replacing its credentials.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$PROJECT_ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Environment file not found: $ENV_FILE" >&2
    echo "Copy .env.example to .env and fill in the required values first." >&2
    exit 1
fi

chmod 600 "$ENV_FILE"
echo "✅ Environment file permissions set to 600: $ENV_FILE"
echo "Restart the bot service separately to load configuration changes."
