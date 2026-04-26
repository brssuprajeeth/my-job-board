#!/usr/bin/env python3
"""
Company Career Portal Scraper — Top 100 Seattle SDE-2 Companies
================================================================
Scrapes career portals directly for mid-level SDE jobs in Seattle area.

Strategy:
  - Greenhouse companies → Greenhouse JSON API (free, no auth)
  - Lever companies → Lever JSON API (free, no auth)
  - Workday/Custom companies → Google Jobs search as proxy

Usage:
    python company_scraper.py                     # Scrape all companies
    python company_scraper.py --push              # Scrape + push to GitHub
    python company_scraper.py --company "Stripe"  # Scrape one company
    python company_scraper.py --ats greenhouse    # Scrape only Greenhouse companies

Setup:
    pip install requests pandas openpyxl
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pandas as pd
import requests

# --- CONFIG ---
XLSX_PATH = "seattle_sde2_companies.xlsx"
GITHUB_REPO_PATH = os.environ.get("JOB_BOARD_REPO", "")
JSON_FILENAME = "jobs_latest.json"
EXISTING_JSON = os.path.join(GITHUB_REPO_PATH, "data", JSON_FILENAME) if GITHUB_REPO_PATH else ""

SEATTLE_LOCATIONS = [
    "seattle", "bellevue", "redmond", "kirkland", "bothell",
    "woodinville", "renton", "tukwila", "kent", "tacoma",
    "everett", "issaquah", "sammamish", "mercer island",
    "greater seattle", "puget sound", "sea-tac",
]

# Locations that only count if paired with "wa" or "washington"
STATE_MARKERS = [", wa", "washington state", ", washington"]

# Reject if location contains these (foreign/other regions)
LOCATION_BLOCKLIST = [
    "poland", "canada", "uk", "london", "ireland", "india",
    "germany", "berlin", "singapore", "japan", "tokyo",
    "australia", "sydney", "france", "paris", "brazil",
    "mexico", "toronto", "vancouver", "montreal", "ottawa",
    "warsaw", "krakow", "bangalore", "hyderabad", "pune",
    "tel aviv", "israel", "amsterdam", "netherlands",
    "new york", "san francisco", "los angeles", "chicago",
    "austin", "denver", "boston", "atlanta", "miami",
    "washington, dc", "washington dc", "d.c.",
]

SDE_TITLE_KEYWORDS = [
    "software engineer", "software developer", "sde", "full stack",
    "backend engineer", "frontend engineer", "fullstack",
    "web developer", "application developer", "platform engineer",
    "systems engineer", "dev engineer", "development engineer",
]

SENIOR_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "lead",
    "director", "vp", "manager", "head of", "architect",
    "distinguished", "fellow",
]
JUNIOR_KEYWORDS = [
    "intern", "entry level", "entry-level", "junior", "jr.",
    "jr ", "new grad", "associate", "level 1", "l1", "sde i", "sde 1",
]

# --- ATS MAPPING ---
# Maps company names to their ATS platform and API-friendly identifiers
GREENHOUSE_COMPANIES = {
    "Airbnb": "airbnb",
    "Netflix": "netflix",
    "DoorDash": "doordash",
    "Stripe": "stripe",
    "Pinterest": "pinterestjobs",
    "Snap Inc.": "snap",
    "Coinbase": "coinbase",
    "Dropbox": "dropbox",
    "Affirm": "affirm",
    "Carta": "carta",
    "Robinhood": "robinhood",
    "Chime": "chime",
    "Brex": "brex",
    "Roblox": "roblox",
    "Databricks": "databricks",
    "Snowflake": "snowflakecomputing",
    "Lyft": "lyft",
    "CrowdStrike": "crowdstrike",
    "Okta": "okta",
    "DocuSign": "docusign",
    "Box": "box",
    "Pure Storage": "purestorage",
    "Nutanix": "nutanixinc",
    "SoFi": "solofinancialinc",
    "Samsara": "samsara",
    "Palantir": "palantir",
    "UiPath": "uipath",
    "Unity": "unity3d",
    "Niantic": "nianticinc",
    "Bungie (Sony)": "bungie",
    "Flexport": "flexport",
    "Bolt": "bolt",
    "Square (Block)": "squareup",
    "Electronic Arts (EA)": "electronicarts",
    "Smartsheet": "smartsheet",
    "Convoy": "convoy",
    "Qualtrics": "qualtrics",
    "Twilio": "twilio",
    "ExtraHop": "extrahop",
    "Tanium": "tanium",
    "Auth0 (Okta)": "auth0",
    "Icertis": "icertis",
}

LEVER_COMPANIES = {
    "Remitly": "remitly",
    "Outreach": "outreach",
    "Highspot": "highspot",
    "Amperity": "amperity",
    "OfferUp": "offerup",
}

# Companies that need Google Jobs fallback (Workday + custom ATS)
GOOGLE_FALLBACK_COMPANIES = [
    "OpenAI", "Meta", "Google", "Apple", "Microsoft",
    "Amazon (AWS)", "NVIDIA", "Oracle (OCI)", "LinkedIn",
    "ByteDance (TikTok)", "Uber", "Coupang", "Adobe",
    "Salesforce (Tableau)", "T-Mobile", "Nordstrom (Tech)",
    "Zillow", "Starbucks (Tech)", "Expedia Group",
    "Disney (Hulu/ESPN)", "Walmart Global Tech", "Boeing",
    "Alaska Airlines", "JP Morgan Chase", "Goldman Sachs",
    "Redfin", "GoDaddy", "F5 Networks", "eBay", "PayPal",
    "Splunk", "VMware (Broadcom)", "Cisco", "Intel", "AMD",
    "Valve", "Avalara", "Apptio (IBM)", "Accenture", "Deloitte",
    "Slalom", "West Monroe", "Ernst & Young (EY)", "PwC", "KPMG",
    "Infosys", "TCS", "Wipro", "Cognizant", "HCL Tech",
    "Capgemini", "EPAM Systems", "LTIMindtree",
]

BLOCKED_COMPANIES = ["amazon", "aws", "a2z", "amazon web services"]

# Default: company scraper looks back 7 days
DEFAULT_DAYS_BACK = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def is_within_days(date_str, days_back):
    """Check if a date string is within the last N days."""
    if not date_str:
        return True  # Keep jobs with no date (can't filter)
    try:
        job_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        cutoff = datetime.now() - timedelta(days=days_back)
        return job_date >= cutoff
    except (ValueError, TypeError):
        return True  # Keep if date is unparseable


# --- FILTERS ---

def is_seattle_area(location_str):
    if not location_str:
        return False
    loc = str(location_str).lower().strip()

    # Reject known non-Seattle locations first
    for block in LOCATION_BLOCKLIST:
        if block in loc:
            return False

    # Match Seattle-area cities directly
    for kw in SEATTLE_LOCATIONS:
        if kw in loc:
            return True

    # Match state markers (", WA" or "Washington" but not "Washington DC")
    for marker in STATE_MARKERS:
        if marker in loc:
            return True

    # "Remote" only counts if it also mentions US/United States
    # (skip pure "Remote" with no country — too broad)
    if "remote" in loc and ("us" in loc or "united states" in loc or "usa" in loc):
        return True

    return False


def is_sde_title(title):
    if not title:
        return False
    t = str(title).lower()
    return any(kw in t for kw in SDE_TITLE_KEYWORDS)


def is_mid_level(title):
    if not title:
        return False
    t = str(title).lower()
    for kw in SENIOR_KEYWORDS:
        if kw in t:
            return False
    for kw in JUNIOR_KEYWORDS:
        if kw in t:
            return False
    return True


def is_blocked_company(company):
    c = (company or "").lower()
    return any(b in c for b in BLOCKED_COMPANIES)


def passes_all_filters(title, location, company=""):
    if is_blocked_company(company):
        return False
    if not is_sde_title(title):
        return False
    if not is_mid_level(title):
        return False
    if not is_seattle_area(location):
        return False
    return True


# --- GREENHOUSE SCRAPER ---

def scrape_greenhouse(company_name, board_token, days_back=DEFAULT_DAYS_BACK):
    """Scrape jobs from Greenhouse public API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    Warning: Greenhouse {company_name} returned {resp.status_code}")
            return []
        data = resp.json()
        jobs = data.get("jobs", [])
    except Exception as e:
        print(f"    Error scraping Greenhouse {company_name}: {e}")
        return []

    results = []
    for job in jobs:
        title = job.get("title", "")
        location = job.get("location", {}).get("name", "")
        job_url = job.get("absolute_url", "")
        updated = job.get("updated_at", "")
        date_posted = updated[:10] if updated else ""

        if not passes_all_filters(title, location, company_name):
            continue
        if not is_within_days(date_posted, days_back):
            continue

        results.append({
            "Job Title": title,
            "Company": company_name,
            "Location": location,
            "Apply Link": job_url,
            "Source": "greenhouse",
            "Date Posted": date_posted,
            "Type": "Full-time",
            "Salary": "",
            "Description": "",
        })

    return results


# --- LEVER SCRAPER ---

def scrape_lever(company_name, lever_slug, days_back=DEFAULT_DAYS_BACK):
    """Scrape jobs from Lever public API."""
    url = f"https://api.lever.co/v0/postings/{lever_slug}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    Warning: Lever {company_name} returned {resp.status_code}")
            return []
        jobs = resp.json()
    except Exception as e:
        print(f"    Error scraping Lever {company_name}: {e}")
        return []

    results = []
    for job in jobs:
        title = job.get("text", "")
        location = job.get("categories", {}).get("location", "")
        job_url = job.get("hostedUrl", "")
        created = job.get("createdAt", 0)
        date_str = datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d") if created else ""

        if not passes_all_filters(title, location, company_name):
            continue
        if not is_within_days(date_str, days_back):
            continue

        results.append({
            "Job Title": title,
            "Company": company_name,
            "Location": location,
            "Apply Link": job_url,
            "Source": "lever",
            "Date Posted": date_str,
            "Type": "Full-time",
            "Salary": "",
            "Description": "",
        })

    return results


# --- JOBSPY FALLBACK (for Google/Workday/Custom companies) ---

def scrape_via_jobspy(company_names, days_back=DEFAULT_DAYS_BACK):
    """Use python-jobspy to search for specific companies on job boards."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print("    Warning: python-jobspy not installed. Skipping job board search.")
        print("    Install with: pip install python-jobspy")
        return []

    hours_old = days_back * 24
    results = []
    for company in company_names:
        if is_blocked_company(company):
            continue
        # Clean company name for search (remove parenthetical notes)
        clean_name = re.sub(r'\s*\(.*?\)\s*', ' ', company).strip()
        search_term = f'"{clean_name}" Software Engineer'
        print(f"    Searching job boards for: {clean_name}...")
        try:
            jobs = scrape_jobs(
                site_name=["indeed", "linkedin", "zip_recruiter"],
                search_term=search_term,
                location="Seattle, WA",
                distance=25,
                results_wanted=10,
                hours_old=hours_old,
                country_indeed="USA",
            )
            for _, row in jobs.iterrows():
                title = str(row.get("title", ""))
                location = str(row.get("location", ""))
                comp = str(row.get("company", ""))

                if passes_all_filters(title, location, comp):
                    lo = row.get("min_amount")
                    hi = row.get("max_amount")
                    iv = row.get("interval", "")
                    salary = ""
                    if pd.notna(lo) and pd.notna(hi):
                        salary = f"${lo:,.0f}-${hi:,.0f} {iv or ''}".strip()
                    elif pd.notna(lo):
                        salary = f"${lo:,.0f}+ {iv or ''}".strip()

                    desc = str(row.get("description", ""))
                    if pd.isna(desc) or desc == "nan":
                        desc = ""

                    results.append({
                        "Job Title": title,
                        "Company": comp,
                        "Location": location,
                        "Apply Link": str(row.get("job_url", "")),
                        "Source": str(row.get("site", "jobboard")),
                        "Date Posted": str(row.get("date_posted", ""))[:10],
                        "Type": str(row.get("job_type", "Full-time")),
                        "Salary": salary,
                        "Description": desc[:150] + "..." if len(desc) > 150 else desc,
                    })
            time.sleep(1)  # Rate limiting
        except Exception as e:
            print(f"    Warning: Error searching for {clean_name}: {e}")

    return results


# --- MAIN ORCHESTRATOR ---

def load_company_list(xlsx_path):
    """Load company list from Excel file."""
    if not os.path.exists(xlsx_path):
        print(f"  Company list not found: {xlsx_path}")
        print(f"  Place 'seattle_sde2_companies.xlsx' in the same directory.")
        return pd.DataFrame()
    df = pd.read_excel(xlsx_path)
    return df


def load_existing_jobs():
    """Load existing jobs to avoid duplicates."""
    if EXISTING_JSON and os.path.exists(EXISTING_JSON):
        try:
            with open(EXISTING_JSON) as f:
                data = json.load(f)
                return data.get("jobs", [])
        except:
            pass
    return []


def scrape_all(company_filter=None, ats_filter=None, days_back=DEFAULT_DAYS_BACK):
    """Run all scrapers and merge results."""
    all_jobs = []

    # --- Greenhouse ---
    if not ats_filter or ats_filter == "greenhouse":
        gh_companies = GREENHOUSE_COMPANIES
        if company_filter:
            gh_companies = {k: v for k, v in gh_companies.items() if company_filter.lower() in k.lower()}

        if gh_companies:
            print(f"\n--- Greenhouse API ({len(gh_companies)} companies, past {days_back} days) ---")
            for company, token in gh_companies.items():
                if is_blocked_company(company):
                    continue
                print(f"  Scraping {company}...")
                jobs = scrape_greenhouse(company, token, days_back)
                print(f"    Found {len(jobs)} matching jobs")
                all_jobs.extend(jobs)
                time.sleep(0.3)

    # --- Lever ---
    if not ats_filter or ats_filter == "lever":
        lv_companies = LEVER_COMPANIES
        if company_filter:
            lv_companies = {k: v for k, v in lv_companies.items() if company_filter.lower() in k.lower()}

        if lv_companies:
            print(f"\n--- Lever API ({len(lv_companies)} companies, past {days_back} days) ---")
            for company, slug in lv_companies.items():
                if is_blocked_company(company):
                    continue
                print(f"  Scraping {company}...")
                jobs = scrape_lever(company, slug, days_back)
                print(f"    Found {len(jobs)} matching jobs")
                all_jobs.extend(jobs)
                time.sleep(0.3)

    # --- Job Board Fallback (for Workday/Custom ATS companies) ---
    if not ats_filter or ats_filter == "jobboard":
        fb_companies = GOOGLE_FALLBACK_COMPANIES
        if company_filter:
            fb_companies = [c for c in fb_companies if company_filter.lower() in c.lower()]

        if fb_companies:
            print(f"\n--- Job Board Search ({len(fb_companies)} companies, past {days_back} days) ---")
            board_jobs = scrape_via_jobspy(fb_companies, days_back)
            print(f"  Found {len(board_jobs)} total matching jobs from boards")
            all_jobs.extend(board_jobs)

    return all_jobs


def dedup_jobs(jobs, existing=None):
    """Deduplicate by apply link."""
    if existing is None:
        existing = []

    seen_links = set()
    # Add existing job links
    for j in existing:
        link = (j.get("Apply Link") or "").lower().strip()
        if link:
            seen_links.add(link)

    unique = []
    for j in jobs:
        link = (j.get("Apply Link") or "").lower().strip()
        if not link or link not in seen_links:
            if link:
                seen_links.add(link)
            unique.append(j)
    return unique


def export_json(jobs, existing_jobs=None):
    """Merge with existing and export."""
    if existing_jobs:
        # Merge: keep existing + add new unique ones
        new_jobs = dedup_jobs(jobs, existing_jobs)
        merged = existing_jobs + new_jobs
        print(f"\n  Existing: {len(existing_jobs)}, New: {len(new_jobs)}, Total: {len(merged)}")
    else:
        merged = dedup_jobs(jobs)
        print(f"\n  Total unique jobs: {len(merged)}")

    payload = {
        "scraped_at": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_jobs": len(merged),
        "experience_range": "3-5 years",
        "location": "Greater Seattle Area",
        "source": "company career portals + job boards",
        "jobs": merged,
    }

    # Save locally
    local_path = f"seattle_swe_jobs_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(local_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"  JSON saved: {local_path}")

    # Save CSV too
    csv_path = local_path.replace(".json", ".csv")
    df = pd.DataFrame(merged)
    df.to_csv(csv_path, index=False)
    print(f"  CSV saved: {csv_path}")

    return payload, local_path


def push_to_github(json_path):
    """Copy latest JSON to repo and push."""
    if not GITHUB_REPO_PATH:
        print("  GITHUB_REPO_PATH not set. Skipping push.")
        print("  Set: export JOB_BOARD_REPO=~/my-job-board")
        return False

    import shutil
    dest = os.path.join(GITHUB_REPO_PATH, "data", JSON_FILENAME)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(json_path, dest)

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    cmds = [
        ["git", "-C", GITHUB_REPO_PATH, "add", "."],
        ["git", "-C", GITHUB_REPO_PATH, "commit", "-m", f"company scrape {today}"],
        ["git", "-C", GITHUB_REPO_PATH, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"    Git error: {result.stderr.strip()}")
            return False

    print(f"  Pushed to GitHub!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Scrape company career portals for Seattle SDE-2 jobs")
    parser.add_argument("--company", type=str, help="Scrape only this company")
    parser.add_argument("--ats", type=str, choices=["greenhouse", "lever", "jobboard"], help="Scrape only this ATS type")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS_BACK, help="Days to look back (default: 7)")
    parser.add_argument("--push", action="store_true", help="Push results to GitHub")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing jobs, start fresh")
    args = parser.parse_args()

    print("=" * 60)
    print("  Company Career Portal Scraper")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Lookback: {args.days} days")
    print(f"  Target: Mid-level SDE, Greater Seattle Area")
    print("=" * 60)

    # Load existing jobs for dedup (unless --fresh)
    existing = [] if args.fresh else load_existing_jobs()
    if existing:
        print(f"\n  Loaded {len(existing)} existing jobs for dedup")

    # Scrape
    jobs = scrape_all(company_filter=args.company, ats_filter=args.ats, days_back=args.days)

    if not jobs:
        print("\n  No jobs found.")
        sys.exit(0)

    # Export
    payload, json_path = export_json(jobs, existing if not args.fresh else None)

    if args.push:
        push_to_github(json_path)

    print(f"\n  Done! {payload['total_jobs']} total jobs in output.")


if __name__ == "__main__":
    main()
