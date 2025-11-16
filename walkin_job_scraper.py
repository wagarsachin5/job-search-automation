#!/usr/bin/env python3
"""
Walk-in + E-commerce Job Scraper (Pune) — dotenv REMOVED

Reads credentials from environment variables:
  EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, RECIPIENT_EMAIL

Sources: Naukri, Indeed, Google search snippets, LinkedIn (public), Shine
Filters: Pune + (walk-in OR e-commerce)
Freshness: heuristic = "today", "just posted", "1 day ago", "just now"
Deduped and emails only NEW jobs tracked in SQLite (seen_jobs.db)
"""
import os
import re
import time
import sqlite3
import smtplib
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "25"))

if not (EMAIL_USER and EMAIL_PASS and RECIPIENT_EMAIL):
    raise SystemExit("Set EMAIL_USER, EMAIL_PASS and RECIPIENT_EMAIL as environment variables (or GH Secrets).")

TARGET_CITIES = ["pune", "pimpri", "pimpri chinchwad", "pcmc", "hadapsar", "baner", "wakad", "kharadi"]
ROLE_KEYWORDS = ["ecommerce", "e-commerce", "amazon", "flipkart", "marketplace", "catalog", "listing", "e commerce"]
WALKIN_KEYWORDS = ["walk in", "walk-in", "walkin", "walkin interview", "walk in interview", "walk-in drive"]

EMAIL_REGEX = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
PHONE_REGEX = r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b"

DB_PATH = Path(__file__).parent / "seen_jobs.db"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------------- Helpers ----------------
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


def extract_contact(text):
    if not text:
        return [], []
    emails = list(set(re.findall(EMAIL_REGEX, text)))
    phones = list(set(re.findall(PHONE_REGEX, text)))
    norm_phones = []
    for p in phones:
        p_clean = re.sub(r"[^\d]", "", p)
        if len(p_clean) > 10:
            p_clean = p_clean[-10:]
        if len(p_clean) == 10:
            norm_phones.append(p_clean)
    return emails, norm_phones


def text_has_city(text):
    if not text:
        return False
    t = text.lower()
    return any(city in t for city in TARGET_CITIES)


def text_matches_role_or_walkin(text):
    if not text:
        return False
    t = text.lower()
    return (any(k in t for k in ROLE_KEYWORDS) or any(w in t for w in WALKIN_KEYWORDS))


def is_recent(text):
    if not text:
        return False
    t = text.lower()
    markers = ["just posted", "posted today", "today", "1 day ago", "posted 1 day ago", "just now"]
    return any(m in t for m in markers)


def fetch(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
        else:
            print(f"Fetch {url} returned status {r.status_code}")
            return None
    except Exception as e:
        print(f"Fetch error {url}: {e}")
        return None


# ---------------- Scrapers ----------------
def scrape_naukri():
    print("Scraping Naukri...")
    jobs = []
    base = "https://www.naukri.com/walkin-jobs"
    html = fetch(base)
    if not html:
        return jobs
    soup = BeautifulSoup(html, "lxml")
    cards = soup.find_all("article")
    for c in cards:
        try:
            text = c.get_text(" ", strip=True)
            if not is_recent(text):
                continue
            if not text_has_city(text) or not text_matches_role_or_walkin(text):
                continue
            a = c.find("a", href=True)
            link = a["href"] if a else None
            title = a.get_text(strip=True) if a else (c.find("h2").get_text(strip=True) if c.find("h2") else "No title")
            comp = c.select_one(".comp-name")
            company = comp.get_text(strip=True) if comp else ""
            emails, phones = extract_contact(text)
            jobs.append({
                "title": title,
                "company": company,
                "location": "Pune",
                "description": text,
                "link": link,
                "source": "Naukri",
                "emails": emails,
                "phones": phones,
            })
        except Exception:
            continue
    return jobs[:MAX_RESULTS]


def scrape_indeed():
    print("Scraping Indeed...")
    jobs = []
    url = "https://in.indeed.com/jobs?q=walk-in+ecommerce&l=Pune"
    html = fetch(url)
    if not html:
        return jobs
    soup = BeautifulSoup(html, "lxml")
    cards = soup.find_all("div", class_=lambda v: v and ("jobsearch-SerpJobCard" in v or "slider_item" in v or "result" in v))
    if not cards:
        cards = soup.find_all("a", attrs={"data-hide-spinner": True})
    for c in cards:
        try:
            text = c.get_text(" ", strip=True)
            if not is_recent(text):
                continue
            if not text_has_city(text) or not text_matches_role_or_walkin(text):
                continue
            a = c.find("a", href=True)
            link = "https://in.indeed.com" + a["href"] if a and a.get("href") and a.get("href").startswith("/") else (a["href"] if a and a.get("href") else None)
            title = c.find("h2") or c.find("a")
            title_text = title.get_text(strip=True) if title else "No title"
            company = c.select_one(".companyName")
            company_text = company.get_text(strip=True) if company else ""
            emails, phones = extract_contact(text)
            jobs.append({
                "title": title_text,
                "company": company_text,
                "location": "Pune",
                "description": text,
                "link": link,
                "source": "Indeed",
                "emails": emails,
                "phones": phones,
            })
        except Exception:
            continue
    return jobs[:MAX_RESULTS]


def scrape_google():
    print("Scraping Google Search snippets...")
    jobs = []
    q = "Walk-in E-commerce jobs Pune"
    url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
    html = fetch(url)
    if not html:
        return jobs
    soup = BeautifulSoup(html, "lxml")
    snippets = soup.select("div.BNeawe.s3v9rd.AP7Wnd") or soup.select("div.BNeawe")
    for s in snippets:
        try:
            text = s.get_text(" ", strip=True)
            if not is_recent(text):
                continue
            if not text_has_city(text) or not text_matches_role_or_walkin(text):
                continue
            emails, phones = extract_contact(text)
            jobs.append({
                "title": "Google snippet",
                "company": "",
                "location": "Pune",
                "description": text,
                "link": url,
                "source": "Google",
                "emails": emails,
                "phones": phones,
            })
        except Exception:
            continue
    return jobs[:MAX_RESULTS]


def scrape_linkedin():
    print("Scraping LinkedIn public search (limited)...")
    jobs = []
    q = "E-commerce Manager"
    url = f"https://www.linkedin.com/jobs/search/?keywords={q.replace(' ', '%20')}&location=Pune"
    html = fetch(url)
    if not html:
        return jobs
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("ul.jobs-search__results-list li") or soup.select(".result-card.job-result-card")
    for c in cards:
        try:
            text = c.get_text(" ", strip=True)
            if not text_has_city(text) or not text_matches_role_or_walkin(text):
                continue
            a = c.find("a", href=True)
            link = a["href"] if a else None
            title_tag = c.select_one(".job-card-list__title") or a
            title = title_tag.get_text(strip=True) if title_tag else "No title"
            company_tag = c.select_one(".job-card-container__company-name")
            company = company_tag.get_text(strip=True) if company_tag else ""
            emails, phones = extract_contact(text)
            jobs.append({
                "title": title,
                "company": company,
                "location": "Pune",
                "description": text,
                "link": link,
                "source": "LinkedIn",
                "emails": emails,
                "phones": phones,
            })
        except Exception:
            continue
    return jobs[:MAX_RESULTS]


def scrape_shine():
    print("Scraping Shine...")
    jobs = []
    url = "https://www.shine.com/job-search/walkin-jobs"
    html = fetch(url)
    if not html:
        return jobs
    soup = BeautifulSoup(html, "lxml")
    cards = soup.find_all("div", class_="result-display__profile")
    for c in cards:
        try:
            text = c.get_text(" ", strip=True)
            if not is_recent(text):
                continue
            if not text_has_city(text) or not text_matches_role_or_walkin(text):
                continue
            a = c.find("a", href=True)
            link = ("https://www.shine.com" + a["href"]) if a and a.get("href") else None
            title = c.find("h2").get_text(strip=True) if c.find("h2") else "No title"
            company = c.select_one(".result-display__profile__company-name")
            company_text = company.get_text(strip=True) if company else ""
            emails, phones = extract_contact(text)
            jobs.append({
                "title": title,
                "company": company_text,
                "location": "Pune",
                "description": text,
                "link": link,
                "source": "Shine",
                "emails": emails,
                "phones": phones,
            })
        except Exception:
            continue
    return jobs[:MAX_RESULTS]


# ---------------- Build & Send Email ----------------
def dedupe_jobs(jobs):
    out = []
    seen_links = set()
    for j in jobs:
        link = j.get("link") or (j.get("title","") + j.get("company",""))
        if not link:
            continue
        key = link.strip()
        if key in seen_links:
            continue
        seen_links.add(key)
        out.append(j)
    return out


def build_email_html(jobs):
    parts = []
    parts.append(f"<h2>Daily Walk-in + E-commerce Jobs (Pune) — {datetime.now().strftime('%Y-%m-%d')}</h2>")
    if not jobs:
        parts.append("<p>No new jobs found in the last 1 day.</p>")
    else:
        parts.append(f"<p>Total new jobs: {len(jobs)}</p>")
        for j in jobs:
            parts.append("<div style='margin-bottom:12px;padding:10px;border:1px solid #e6e6e6;border-radius:6px;'>")
            parts.append(f"<h3 style='margin:0'>{html_escape(j.get('title'))}</h3>")
            if j.get('company'):
                parts.append(f"<p style='margin:4px 0'><b>Company:</b> {html_escape(j.get('company'))}</p>")
            parts.append(f"<p style='margin:4px 0'><b>Source:</b> {html_escape(j.get('source'))} ")
            if j.get('location'):
                parts.append(f" - {html_escape(j.get('location'))}</p>")
            parts.append(f"<p style='margin:6px 0'>{html_escape((j.get('description') or '')[:600])}...</p>")
            if j.get('link'):
                parts.append(f"<p style='margin:6px 0'><a href=\"{j.get('link')}\">Open job/link</a></p>")
            if j.get('emails'):
                parts.append(f"<p style='margin:6px 0'><b>Emails:</b> {', '.join(map(html_escape, j.get('emails')))}</p>")
            if j.get('phones'):
                parts.append(f"<p style='margin:6px 0'><b>Phones:</b> {', '.join(map(html_escape, j.get('phones')))}</p>")
            parts.append("</div>")
    return "\n".join(parts)


def html_escape(s):
    import html as _html
    return _html.escape(s or "")


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=60) as s:
        s.ehlo()
        if EMAIL_PORT == 587:
            s.starttls()
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())


# ---------------- Main ----------------
def main():
    conn = init_db()
    all_jobs = []
    try:
        all_jobs.extend(scrape_naukri())
    except Exception as e:
        print("Naukri scrape failed:", e)
    try:
        all_jobs.extend(scrape_indeed())
    except Exception as e:
        print("Indeed scrape failed:", e)
    try:
        all_jobs.extend(scrape_google())
    except Exception as e:
        print("Google scrape failed:", e)
    try:
        all_jobs.extend(scrape_linkedin())
    except Exception as e:
        print("LinkedIn scrape failed:", e)
    try:
        all_jobs.extend(scrape_shine())
    except Exception as e:
        print("Shine scrape failed:", e)

    print(f"Found {len(all_jobs)} raw candidates")

    deduped = dedupe_jobs(all_jobs)

    new_jobs = []
    for j in deduped:
        link = j.get('link') or (j.get('title','') + j.get('company',''))
        if not link:
            continue
        if not is_seen(conn, link):
            new_jobs.append(j)
            mark_seen(conn, link)

    print(f"New jobs to send: {len(new_jobs)}")

    html_body = build_email_html(new_jobs)
    subject = f"Walk-in+Ecom Jobs (Pune) — {datetime.now().strftime('%Y-%m-%d')}"
    try:
        send_email(subject, html_body)
        print("Email sent to", RECIPIENT_EMAIL)
    except Exception as e:
        print("Failed to send email:", e)


if __name__ == '__main__':
    main()
