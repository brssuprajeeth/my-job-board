#!/usr/bin/env python3
"""
Seattle Mid-Level Software Engineer Job Scraper (3-5 years exp)
================================================================
Scrapes job postings from Indeed, LinkedIn, ZipRecruiter, and Glassdoor.
Outputs CSV + JSON. Optionally auto-pushes JSON to a GitHub repo so your
hosted job board picks it up automatically.

Usage:
    python job_scraper.py                       # Default: past 24 hours
    python job_scraper.py --hours 48            # Wider window
    python job_scraper.py --push                # Auto-push to GitHub Pages
    python job_scraper.py --output my_jobs      # Custom filename prefix

Setup:
    pip install python-jobspy pandas

Auto-deploy (optional):
    1. Create a GitHub repo (e.g. "my-job-board")
    2. Clone it locally and set GITHUB_REPO_PATH below
    3. Run with --push to auto-commit & push JSON to the repo
    4. Your GitHub Pages site will serve the updated data
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from jobspy import scrape_jobs
import pandas as pd


# --- CONFIG ---
SEARCH_TERMS = [
    "Software Engineer",
    "Software Developer",
    "Full Stack Engineer",
    "Backend Engineer",
]

LOCATION = "Seattle, WA"
DISTANCE_MILES = 50
RESULTS_PER_SEARCH = 50
TARGET_MIN_YRS = 3
TARGET_MAX_YRS = 5

# Set this to your local GitHub repo clone path for --push
GITHUB_REPO_PATH = os.environ.get("JOB_BOARD_REPO", "")
JSON_FILENAME = "jobs_latest.json"   # auto-fetched by the website


# --- EXPERIENCE DETECTION ---

SENIOR_TITLE_KW = [
    "senior", "sr.", "sr ", "staff", "principal", "lead",
    "director", "vp", "manager", "head of", "architect",
    "distinguished", "fellow",
]
JUNIOR_TITLE_KW = [
    "intern", "entry level", "entry-level", "junior", "jr.",
    "jr ", "new grad", "associate", "level 1", "l1", "sde i",
    "sde 1",
]
MID_TITLE_KW = [
    "mid", " ii", " iii", "level 2", "level 3", "l3", "l4",
    "sde ii", "sde iii", "sde 2", "sde 3", "engineer ii",
    "engineer iii", "developer ii", "developer iii",
]

EXP_RANGE_RE = re.compile(
    r'(\d{1,2})\s*(?:\+|to|-)\s*(\d{1,2})?\s*(?:\+)?\s*years?',
    re.IGNORECASE,
)
EXP_SINGLE_RE = re.compile(
    r'(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:experience|exp)',
    re.IGNORECASE,
)


def extract_experience_years(text):
    import pandas as pd
    if not text or pd.isna(text):
        return None, None
    snippet = text[:2000].lower()
    ranges = EXP_RANGE_RE.findall(snippet)
    singles = EXP_SINGLE_RE.findall(snippet)
    candidates = []
    for match in ranges:
        lo = int(match[0])
        hi = int(match[1]) if match[1] else lo
        candidates.append((lo, hi))
    for match in singles:
        yr = int(match)
        candidates.append((yr, yr))
    if not candidates:
        return None, None
    return candidates[0]


def is_mid_level(title, description=""):
    import pandas as pd
    if pd.isna(title): title = ""
    if pd.isna(description): description = ""
    t = str(title).lower()
    d = str(description).lower()

    for kw in SENIOR_TITLE_KW:
        if kw in t:
            return False
    for kw in JUNIOR_TITLE_KW:
        if kw in t:
            return False

    lo, hi = extract_experience_years(d)
    if lo is not None:
        if lo > TARGET_MAX_YRS:
            return False
        if hi is not None and hi < TARGET_MIN_YRS:
            return False
        if lo <= TARGET_MAX_YRS and (hi is None or hi >= TARGET_MIN_YRS):
            return True

    for kw in MID_TITLE_KW:
        if kw in t:
            return True

    generic = [
        "software engineer", "software developer", "full stack",
        "backend engineer", "frontend engineer", "sde",
        "web developer", "application developer", "fullstack",
    ]
    for gt in generic:
        if gt in t:
            return True

    return False


# --- SCRAPING ---

def scrape_all_jobs(hours_back=24):
    all_jobs = []
    sites = ["indeed", "linkedin", "zip_recruiter", "glassdoor"]

    for term in SEARCH_TERMS:
        print(f"\n  Searching: '{term}' across {', '.join(sites)}...")
        try:
            jobs = scrape_jobs(
                site_name=sites,
                search_term=term,
                location=LOCATION,
                distance=DISTANCE_MILES,
                results_wanted=RESULTS_PER_SEARCH,
                hours_old=hours_back,
                country_indeed="USA",
            )
            print(f"   Found {len(jobs)} raw results")
            all_jobs.append(jobs)
        except Exception as e:
            print(f"   Warning: Error scraping '{term}': {e}")

    if not all_jobs:
        print("\n  No results found from any source.")
        return pd.DataFrame()

    df = pd.concat(all_jobs, ignore_index=True)
    print(f"\n  Total raw results: {len(df)}")

    df["_dedup"] = (
        df["title"].str.lower().str.strip() + "|" +
        df["company"].str.lower().str.strip()
    )
    df = df.drop_duplicates(subset="_dedup").drop(columns=["_dedup"])
    print(f"   After dedup: {len(df)}")

    # Block current/past employers
    BLOCKED_COMPANIES = ["amazon", "aws", "a2z", "amazon web services"]
    df = df[~df["company"].str.lower().str.contains('|'.join(BLOCKED_COMPANIES), na=False)]
    print(f"   After employer filter: {len(df)}")

    # Mid-level filter (3-5 years)
    mask = df.apply(
        lambda r: is_mid_level(r.get("title", ""), r.get("description", "")),
        axis=1,
    )
    df = df[mask]
    print(f"   After mid-level filter (3-5 yrs): {len(df)}")

    return df


def clean_and_format(df):
    keep = [
        "title", "company", "location", "job_url", "site",
        "date_posted", "min_amount", "max_amount", "interval",
        "job_type", "description",
    ]
    existing = [c for c in keep if c in df.columns]
    df = df[existing].copy()

    if "min_amount" in df.columns and "max_amount" in df.columns:
        def fmt_salary(row):
            lo, hi = row.get("min_amount"), row.get("max_amount")
            iv = row.get("interval", "")
            if pd.notna(lo) and pd.notna(hi):
                return f"${lo:,.0f}-${hi:,.0f} {iv or ''}".strip()
            if pd.notna(lo):
                return f"${lo:,.0f}+ {iv or ''}".strip()
            return ""
        df["salary"] = df.apply(fmt_salary, axis=1)
        df.drop(columns=["min_amount", "max_amount", "interval"], errors="ignore", inplace=True)

    if "description" in df.columns:
        df["description"] = df["description"].str[:300] + "..."

    df.rename(columns={
        "title": "Job Title", "company": "Company", "location": "Location",
        "job_url": "Apply Link", "site": "Source", "date_posted": "Date Posted",
        "job_type": "Type", "salary": "Salary", "description": "Description",
    }, inplace=True)

    return df


# --- EXPORT ---

def export_csv(df, name):
    path = f"{name}.csv"
    df.to_csv(path, index=False)
    print(f"  CSV saved: {path}")
    return path


def export_json(df, name):
    path = f"{name}.json"
    records = df.to_dict(orient="records")
    payload = {
        "scraped_at": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_jobs": len(records),
        "experience_range": f"{TARGET_MIN_YRS}-{TARGET_MAX_YRS} years",
        "location": LOCATION,
        "jobs": records,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"  JSON saved: {path}")
    return path


def push_to_github(json_path):
    if not GITHUB_REPO_PATH:
        print("  GITHUB_REPO_PATH not set. Skipping auto-push.")
        print("   Set it in the script or via: export JOB_BOARD_REPO=/path/to/repo")
        return False

    dest = os.path.join(GITHUB_REPO_PATH, "data", JSON_FILENAME)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    import shutil
    shutil.copy2(json_path, dest)

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    cmds = [
        ["git", "-C", GITHUB_REPO_PATH, "add", "."],
        ["git", "-C", GITHUB_REPO_PATH, "commit", "-m", f"jobs update {today}"],
        ["git", "-C", GITHUB_REPO_PATH, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"   Git error: {result.stderr.strip()}")
            return False

    print(f"  Pushed to GitHub! Site will update in ~1 min.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Scrape mid-level (3-5yr) SWE jobs in Seattle")
    parser.add_argument("--hours", type=int, default=24, help="Hours lookback (default: 24)")
    parser.add_argument("--output", type=str, default=None, help="Output filename prefix")
    parser.add_argument("--push", action="store_true", help="Auto-push JSON to GitHub Pages repo")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    output_name = args.output or f"seattle_swe_jobs_{today}"

    print("=" * 60)
    print(f"  Seattle Mid-Level SWE Job Scraper")
    print(f"  Date: {today}")
    print(f"  Lookback: {args.hours} hours")
    print(f"  Experience: {TARGET_MIN_YRS}-{TARGET_MAX_YRS} years")
    print("=" * 60)

    df = scrape_all_jobs(hours_back=args.hours)

    if df.empty:
        print("\nNo mid-level jobs found. Try --hours 48 or check network.")
        sys.exit(0)

    df = clean_and_format(df)
    csv_path = export_csv(df, output_name)
    json_path = export_json(df, output_name)

    if args.push:
        push_to_github(json_path)

    print(f"\n  Done! {len(df)} mid-level (3-5 yr) SWE jobs collected.")
    print(f"   CSV:  {csv_path}")
    print(f"   JSON: {json_path}")


if __name__ == "__main__":
    main()
