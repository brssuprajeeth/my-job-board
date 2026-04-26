#!/bin/bash
# ============================================================
# Daily Job Scraper Runner
# ============================================================
# Runs both scrapers, merges results, and pushes to GitHub.
#
# Usage:
#   ./daily_scrape.sh          # Run both scrapers + push
#   ./daily_scrape.sh --no-push # Run without pushing
#
# Cron (run daily at 8am):
#   0 8 * * * cd ~/my-job-board && ./daily_scrape.sh >> /tmp/jobscraper.log 2>&1
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source venv/bin/activate

export JOB_BOARD_REPO="$SCRIPT_DIR"

PUSH_FLAG=""
if [ "$1" != "--no-push" ]; then
    PUSH_FLAG="--push"
fi

echo ""
echo "========================================"
echo " Daily Job Scrape — $(date '+%Y-%m-%d %H:%M')"
echo "========================================"

# Step 1: Run company career portal scraper (Greenhouse + Lever + JobSpy)
echo ""
echo "--- Step 1: Company Career Portals ---"
python3 company_scraper.py $PUSH_FLAG

# Step 2: Run job board scraper (Indeed + LinkedIn + ZipRecruiter)
echo ""
echo "--- Step 2: Job Boards (Indeed/LinkedIn/ZipRecruiter) ---"
python3 job_scraper.py $PUSH_FLAG

echo ""
echo "========================================"
echo " All done! $(date '+%Y-%m-%d %H:%M')"
echo "========================================"
