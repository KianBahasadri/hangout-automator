#!/usr/bin/env bash
# Aggregate gate for plan.md — exits non-zero unless every check passes.
# Each check is a separate labelled step with its own message.
#
# Check 3 (alembic upgrade head + alembic check) WRITES TO A DATABASE.
# Never run this script pointed at anything but localhost: refuse loudly
# (exit 2) unless TEST_DATABASE_URL is set or DATABASE_URL points at
# localhost. Checks 3-6 fail until their phases land; that is expected.

set -u

checks_pass=1
section() {
    printf '\n=== %s ===\n' "$1"
}

# --- Safety guard: only ever migrate a local database ----------------------
section "Safety guard (database target)"

guard_error=""
if [ -n "${TEST_DATABASE_URL:-}" ]; then
    echo "OK: TEST_DATABASE_URL is set; database target allowed."
elif [ -n "${DATABASE_URL:-}" ]; then
    case "$DATABASE_URL" in
        *localhost*|*127.0.0.1*) echo "OK: DATABASE_URL points at localhost; database target allowed." ;;
        *)
            guard_error="DATABASE_URL does not point at localhost"
            ;;
    esac
else
    guard_error="neither TEST_DATABASE_URL nor DATABASE_URL is set"
fi

if [ -n "$guard_error" ]; then
    echo "REFUSED: $guard_error." >&2
    echo "verify_plan.sh runs migrations (alembic upgrade head); it will not" >&2
    echo "run against anything but a local database. Set TEST_DATABASE_URL to a" >&2
    echo "local Postgres, or set DATABASE_URL to a localhost URL, and re-run." >&2
    exit 2
fi

# --- Check 1: test suite ----------------------------------------------------
section "Check 1: pytest"
if uv run --group dev pytest; then
    echo "PASS: pytest green."
else
    echo "FAIL: pytest red."
    checks_pass=0
fi

# --- Check 2: ruff ----------------------------------------------------------
section "Check 2: ruff"
ruff_ok=1
if uv run ruff check .; then
    echo "PASS: ruff check."
else
    echo "FAIL: ruff check."
    ruff_ok=0
fi
if uv run ruff format --check .; then
    echo "PASS: ruff format --check."
else
    echo "FAIL: ruff format --check."
    ruff_ok=0
fi
[ "$ruff_ok" -eq 0 ] && checks_pass=0

# --- Check 3: alembic upgrade head + alembic check --------------------------
section "Check 3: alembic"
if uv run alembic upgrade head && uv run alembic check; then
    echo "PASS: alembic upgrade head clean, alembic check reports no pending diff."
else
    echo "FAIL: alembic upgrade/check. This needs the Phase 2 compose Postgres"
    echo "      running (scripts/db_up.sh) and expects no pending autogenerate diff."
    checks_pass=0
fi

# --- Check 4: no BackgroundScheduler in the web process ---------------------
section "Check 4: scheduler out of app/main.py"
if grep -rn "BackgroundScheduler" app/main.py; then
    echo "FAIL: BackgroundScheduler still present in app/main.py (Phase 4)."
    checks_pass=0
else
    echo "PASS: no BackgroundScheduler in app/main.py."
fi

# --- Check 5: no SQLite anywhere in app/ ------------------------------------
section "Check 5: no sqlite/PRAGMA in app/"
if grep -rniIE "sqlite|PRAGMA" app/; then
    echo "FAIL: sqlite/PRAGMA still present in app/ (Phase 2)."
    checks_pass=0
else
    echo "PASS: no sqlite/PRAGMA references anywhere in app/."
fi

# --- Check 6: tenancy + worker concurrency tests exist and pass -------------
section "Check 6: tenant isolation + worker concurrency tests"
if [ -f tests/test_tenant_isolation.py ] && [ -f tests/test_worker_concurrency.py ]; then
    if uv run --group dev pytest tests/test_tenant_isolation.py tests/test_worker_concurrency.py; then
        echo "PASS: tenant isolation and worker concurrency tests exist and pass."
    else
        echo "FAIL: tenant isolation / worker concurrency tests exist but fail (Phases 3, 4)."
        checks_pass=0
    fi
else
    echo "FAIL: tests/test_tenant_isolation.py or tests/test_worker_concurrency.py"
    echo "      missing (Phases 3, 4)."
    checks_pass=0
fi

# --- Check 7: advisory lock regression test exists and passes ---------------
section "Check 7: advisory lock regression test"
if [ -f tests/test_advisory_lock.py ]; then
    if uv run --group dev pytest tests/test_advisory_lock.py; then
        echo "PASS: advisory lock regression test exists and passes."
    else
        echo "FAIL: tests/test_advisory_lock.py exists but fails."
        checks_pass=0
    fi
else
    echo "FAIL: tests/test_advisory_lock.py missing (post-plan audit F3)."
    checks_pass=0
fi

# --- Summary ----------------------------------------------------------------
section "Result"
if [ "$checks_pass" -eq 1 ]; then
    echo "verify_plan.sh: ALL CHECKS PASS."
    exit 0
else
    echo "verify_plan.sh: one or more checks failed."
    exit 1
fi
