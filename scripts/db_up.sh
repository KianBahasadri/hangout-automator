#!/usr/bin/env bash
# Start the local-development Postgres container and wait until it accepts
# connections. Requires Docker. Refuses to run if it would touch anything but
# the compose-defined container.
set -eu

cd "$(dirname "$0")/.."

docker compose up -d postgres

echo "Waiting for Postgres to accept connections..."
for _ in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U hangout -d hangout >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! docker compose exec -T postgres pg_isready -U hangout -d hangout >/dev/null 2>&1; then
    echo "Postgres did not become ready in 30s." >&2
    echo "Check the container: docker compose logs postgres" >&2
    exit 1
fi

# The test suite (tests/conftest.py) targets its own database.
if ! docker compose exec -T postgres psql -U hangout -d hangout -tAc \
        "SELECT 1 FROM pg_database WHERE datname='hangout_test'" | grep -q 1; then
    docker compose exec -T postgres psql -U hangout -d hangout -c "CREATE DATABASE hangout_test" >/dev/null
fi

echo "Postgres ready: postgresql://hangout:hangout@localhost:5432/hangout (test db: hangout_test)"
