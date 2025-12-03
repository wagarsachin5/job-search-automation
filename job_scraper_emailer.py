#!/usr/bin/env python3
"""
job_scraper_emailer.py
Daily Job Search: Naukri (10) + LinkedIn (10)
SMTP: STARTTLS (port 587)

Environment (.env or GitHub Secrets):
  EMAIL_HOST (smtp.gmail.com)
  EMAIL_PORT (587)
  EMAIL_USER
  EMAIL_PASS (Gmail App Password)
  RECIPIENT_EMAIL
  NAUKRI_QUERY (optional)
  LINKEDIN_QUERY (optional)
  MAX_RESULTS (optional, default 10 per source)
"""
import os
import re
import time
import sqlite3
import smtplib
import html
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# optional dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # ok in GitHub Actions (secrets used)

# Optional Playwright fallback (LinkedIn pages may need rendering)
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ---------------- CONFIG ----------------
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

NAUKRI_QUERY = os.getenv("NAUKRI_QUERY", "E-commerce Manager")
LINKEDIN_QUERY = os.getenv("LINKEDIN_QUERY", "Ecommerce Manager")
MAX_PER_SOURCE = int(os.getenv("MAX_RESULTS", "10"))

if not (EMAIL_USER and EMAIL_PASS and RECIPIENT_EMAIL):
    raise SystemExit("Set EMAIL_USER, EMAIL_PASS and RECIPIENT_EMAIL (env or secrets).")

DB_PATH = Path(__file__).parent / "seen_jobs.db"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TARGET_CITIES = ["pune", "mumbai", "bangalore", "bengaluru"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b")

# ---------------- DB Helpers ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            link TEXT PRIMARY KEY,
            first_seen INTEGER
        )
    """)
    conn.commit()
    return conn

def is_seen(conn, link):
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen WHERE link = ?", (link,))
    return c.fetchone() is not None

def mark_seen(conn, link):
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO seen (link, first_seen) VALUES (?, ?)", (link, int(time.time())))
    conn.commit()

# ---------------- Freshness helper (1 day) ----------------
def parse_freshness_text(text):
    """Return True if text indicates a post within last 24 hours."""
    if not text:
        return False
    t = text.lower()
    if any(k in t for k in ("just posted", "just now", "posted today", "today", "few seconds", "few minutes", "minutes ago")):
        return True
    # patterns like "X hours ago"
    m = re.search(r"(\d+)\s+hour", t)
    if m:
        try:
            hours = int(m.group(1))
            return hours <= 24
        except:
            pass
    # 1 day ago or 24 hours
    if "1 day ago" in t or "24 hours" in t:
        return True
    return False

# ---------------- Fetch helpers ----------------
def fetch_requests(url, headers=None, timeout=15):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"requests fetch error {url}: {e}")
    return None

def fetch_playwright(url, timeout=30000):
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout)
            page.wait_for_timeout(1500)
            content = page.content()
            page.close()
            browser.close()
            return content
    except PWTimeoutError as e:
        print("playwright timeout", e)
    except Exception as e:
        print("playwright error", e)
    return None

def fetch_page(url):
    # try requests first, fallback to playwright if necessary
    html_text = fetch_requests(url)
    if html_text:
        return html_text
    return fetch_playwright(url)

# ---------------- Naukri scraper (limit 10 fresh) ----------------
def scrape_naukri(limit=MAX_PER_SOURCE):
    print("Scraping Naukri...")
    q = requests.utils.requote_uri(NAUKRI_QUERY)
    url = f"https://www.naukri.com/{q}-jobs"
    page = fetch_page(url)
    jobs = []
    if not page:
        return jobs
    soup = BeautifulSoup(page, "html.parser")
    cards = soup.select("article.jobTuple") or soup.select("div.jobTuple") or soup.select("div.list")
    count = 0
    for c in cards:
        if count >= limit:
            break
        try:
            # find anchor
            a = c.select_one("a")
            href = a["href"] if a and a.has_attr("href") else None
            title_el = c.select_one(".jobTitle") or a
            title = title_el.get_text(strip=True) if title_el else ""
            comp = c.select_one(".companyName") or c.select_one(".subTitle")
            company = comp.get_text(strip=True) if comp else ""
            loc = c.select_one(".location")
            location = loc.get_text(strip=True) if loc else ""
            # freshness
            fresh_el = c.select_one(".fleft.grey-text") or c.select_one(".posted") or c.select_one(".metaInfo")
            fresh_text = fresh_el.get_text(strip=True) if fresh_el else ""
            if not parse_freshness_text(fresh_text):
                continue
            # city filter
            combined = " ".join([title, company, location]).lower()
            if not any(city in combined for city in TARGET_CITIES):
                continue
            jobs.append({"title": title, "company": company, "location": location, "link": href, "source": "Naukri"})
            count += 1
        except Exception:
            continue
    print(f"Naukri -> collected {len(jobs)}")
    return jobs

# ---------------- LinkedIn scraper (limit 10 fresh) ----------------
def scrape_linkedin(limit=MAX_PER_SOURCE):
    print("Scraping LinkedIn...")
    q = requests.utils.requote_uri(LINKEDIN_QUERY)
    url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location=India&sortBy=DD"
    page = fetch_page(url)
    jobs = []
    if not page:
        return jobs
    soup = BeautifulSoup(page, "html.parser")
    cards = soup.select("ul.jobs-search__results-list li") or soup.select("div.base-card")
    count = 0
    for c in cards:
        if count >= limit:
            break
        try:
            a = c.select_one("a")  # link element
            href = a["href"] if a and a.has_attr("href") else None
            title_el = c.select_one(".job-card-list__title") or a
            title = title_el.get_text(strip=True) if title_el else ""
            comp = c.select_one(".job-card-container__company-name") or c.select_one(".result-card__subtitle")
            company = comp.get_text(strip=True) if comp else ""
            loc = c.select_one(".job-card-container__metadata-item") or c.select_one(".job-result-card__location")
            location = loc.get_text(strip=True) if loc else ""
            # freshness
            fresh_el = c.select_one("time") or c.select_one(".job-search-card__listdate")
            fresh_text = fresh_el.get_text(strip=True) if fresh_el else ""
            if not parse_freshness_text(fresh_text):
                continue
            combined = " ".join([title, company, location]).lower()
            if not any(city in combined for city in TARGET_CITIES):
                continue
            jobs.append({"title": title, "company": company, "location": location, "link": href, "source": "LinkedIn"})
            count += 1
        except Exception:
            continue
    print(f"LinkedIn -> collected {len(jobs)}")
    return jobs

# ---------------- enrichment & contact extraction ----------------
def extract_contacts_from_text(text):
    emails = EMAIL_RE.findall(text or "") or []
    phones = PHONE_RE.findall(text or "") or []
    # normalize phones to last 10 digits
    phones_norm = []
    for p in set(phones):
        pclean = re.sub(r"[^\d]", "", p)
        if len(pclean) > 10:
            pclean = pclean[-10:]
        if len(pclean) == 10:
            phones_norm.append(pclean)
    return list(set(emails)), phones_norm

def enrich_job(job):
    link = job.get("link")
    if not link:
        job.setdefault("description", "")
        job.setdefault("emails", [])
        job.setdefault("phones", [])
        return job
    page = fetch_page(link)
    if page:
        soup = BeautifulSoup(page, "html.parser")
        # try common job description selectors
        sel = soup.select_one("#jobDescriptionText") or soup.select_one(".jd-desc") or soup.select_one(".job-desc") or soup.select_one(".description")
        full = sel.get_text(" ", strip=True) if sel else soup.get_text(" ", strip=True)[:3000]
        job["description"] = (job.get("description","") or "") + "\n\n" + full
    else:
        job["description"] = job.get("description","")
    combined = " ".join([job.get("title",""), job.get("company",""), job.get("location",""), job.get("description","")])
    emails, phones = extract_contacts_from_text(combined)
    job["emails"] = emails
    job["phones"] = phones
    return job

# ---------------- email builder & sender (STARTTLS - 587) ----------------
def build_email_html(jobs):
    parts = [f"<h2>Daily Job Search — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</h2>"]
    if not jobs:
        parts.append("<p>No new jobs found (fresh within 1 day).</p>")
    else:
        parts.append(f"<p>Total new jobs: {len(jobs)}</p>")
        for j in jobs:
            parts.append("<div style='margin:8px;padding:8px;border:1px solid #ddd;border-radius:6px;'>")
            parts.append(f"<h3>{html.escape(j.get('title',''))}</h3>")
            parts.append(f"<p><b>Company:</b> {html.escape(j.get('company',''))}</p>")
            parts.append(f"<p><b>Location:</b> {html.escape(j.get('location',''))}</p>")
            if j.get("link"):
                parts.append(f"<p><a href='{j.get('link')}' target='_blank'>Open job link</a></p>")
            if j.get("emails"):
                parts.append(f"<p><b>Emails:</b> {', '.join(html.escape(e) for e in j.get('emails'))}</p>")
            if j.get("phones"):
                parts.append(f"<p><b>Phones:</b> {', '.join(html.escape(p) for p in j.get('phones'))}</p>")
            parts.append("</div>")
    return "\n".join(parts)

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    try:
        s = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=60)
        s.ehlo()
        if EMAIL_PORT == 587:
            s.starttls()
            s.ehlo()
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        s.quit()
        print("Email sent via STARTTLS.")
    except Exception as e:
        print("Failed to send email:", e)

# ---------------- main ----------------
def main():
    conn = init_db()
    all_jobs = []
    all_jobs.extend(scrape_naukri(limit=10))
    all_jobs.extend(scrape_linkedin(limit=10))

    print(f"Found candidate listings: {len(all_jobs)} (before enrich/filter)")

    enriched = []
    for j in all_jobs:
        try:
            enriched.append(enrich_job(j))
        except Exception as e:
            print("Error enriching", e)

    # filter by target cities and unseen
    new_jobs = []
    for j in enriched:
        combined = " ".join([j.get("title",""), j.get("location",""), j.get("description","")]).lower()
        if not any(city in combined for city in TARGET_CITIES):
            continue
        link = j.get("link") or (j.get("title","")+j.get("company",""))
        if not is_seen(conn, link):
            new_jobs.append(j)
            mark_seen(conn, link)

    print(f"Filtered & new jobs to send: {len(new_jobs)}")
    subject = f"Daily Job Search — {datetime.utcnow().strftime('%Y-%m-%d')}"
    html_body = build_email_html(new_jobs)
    send_email(subject, html_body)

if __name__ == "__main__":
    main()
