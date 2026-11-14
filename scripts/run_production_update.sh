#!/bin/bash
# ==============================================================================
# BitcoinScore Production Update & Auto-Recovery Pipeline
# ==============================================================================
# Supports:
#   1. Regular scheduled run:      ./scripts/run_production_update.sh
#   2. Catchup / auto-recovery:    ./scripts/run_production_update.sh --catchup
#
# If run with --catchup:
#   - Skips if current hour < 15 (before scheduled time).
#   - Skips if data was already updated today (either by a prior run or by fallback).
#   - If 15:00 run was missed (e.g. server was offline) and fallback hasn't updated yet:
#     -> Immediately executes update and cancels active/queued GitHub Actions fallback!
# ==============================================================================
set -eo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="/tmp/bitcoinscore.lock"

# Non-blocking lock to prevent overlapping runs
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Another BitcoinScore update is already running. Exiting cleanly."
    exit 0
fi

cd "$REPO_DIR"

MODE="${1:-}"

# 1. Fetch latest remote state
git fetch origin main -q || true

# 2. If running in catchup mode, check whether an update is actually needed
if [ "$MODE" = "--catchup" ]; then
    CURRENT_HOUR=$(date +%-H)
    if [ "$CURRENT_HOUR" -lt 15 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Catchup: Current hour ($CURRENT_HOUR) is before 15:00. Skipping."
        exit 0
    fi

    # Check if origin/main was already updated today (e.g. by fallback or manual dispatch)
    if git show origin/main:data/data.json 2>/dev/null | python3 tools/check_freshness.py - >/dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Catchup: origin/main is already updated for today. Syncing and exiting."
        git reset --hard origin/main -q
        exit 0
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Catchup: Missed schedule detected (server was offline at 15:00 and fallback hasn't run). Starting auto-recovery update now!"
fi

# 3. Cancel any in-progress or queued GitHub fallback runs before we start
python3 tools/cancel_fallback.py || true

# 4. Fast-forward local repo to origin/main
git reset --hard origin/main -q

# 5. Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Ensure dependencies are satisfied
pip install -r scraper/requirements.txt -q

# 6. Execute scraper — writes to data/
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running scraper..."
python -m scraper.scraper

# 7. Auxiliary tools
python3 tools/generate_report.py || true
python3 tools/generate_ics.py || true

# 8. Export data/ -> web/
python3 tools/export_web.py

# 9. Commit changes in data/ and web/
git add data/ web/
git commit -m "chore: update data $(date +%Y-%m-%d)" 2>&1 || true

# 10. Push to origin main with rebase retry
if ! git push origin main 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Push rejected — rebasing onto origin/main..."
    if git pull --rebase origin main 2>&1; then
        git push origin main 2>&1
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: rebase conflict — aborting."
        git rebase --abort
        exit 1
    fi
fi

# 11. Cancel any fallback run that may have been triggered on GitHub Actions while scraper was running
python3 tools/cancel_fallback.py || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Update completed and pushed successfully."
