#!/usr/bin/env bash
set -euo pipefail
umask 077

origin="${1:-}"
data_path="${2:-}"
if [[ ! "$origin" =~ ^https://[^/]+(:[0-9]+)?$ ]]; then
    echo "Usage: bash setup.sh https://index.example.com /absolute/data/path" >&2
    exit 1
fi
if [[ "$data_path" != /* ]]; then
    echo "The data path must be absolute." >&2
    exit 1
fi
if [[ -e .env ]]; then
    echo ".env already exists; refusing to overwrite it." >&2
    exit 1
fi

repo="https://raw.githubusercontent.com/finalbillybong/index-inbox/main"
curl -fsSLo compose.yaml "$repo/compose.yaml"
webhook_secret="$(openssl rand -hex 32)"
setup_token="$(openssl rand -hex 32)"

{
    printf 'INDEX_DATA_PATH=%s\n' "$data_path"
    printf 'AUTH_PROVIDER=local\n'
    printf 'AUTH_ALLOWED_ORIGINS=%s\n' "$origin"
    printf 'AUTH_COOKIE_SECURE=true\n'
    printf 'WEBHOOK_SECRET=%s\n' "$webhook_secret"
    printf 'LOCAL_SETUP_TOKEN=%s\n' "$setup_token"
} > .env

mkdir -p "$data_path"
docker compose pull
docker compose run --rm --no-deps -T --user 0 --entrypoint chown index-inbox 1000:1000 /data </dev/null
docker compose up -d

echo
echo "Index Inbox is running."
echo "Open: $origin"
echo "First-run setup token: $setup_token"
echo "After creating the owner, remove LOCAL_SETUP_TOKEN from .env and run:"
echo "  docker compose up -d --force-recreate"
