#!/usr/bin/env python3
"""
job_scraper_emailer.py
Updated: filters by target cities (Option B - scans title/location/description),
extracts recruiter emails and phone numbers (when available),
and emails only NEW jobs (tracked in SQLite).

Platforms scraped: Naukri, LinkedIn, Bing (web search)
Dependencies:
  pip install playwright requests beautifulsoup4 python-dotenv
  python -m playwright install

Environment (set as GitHub secrets or .env):
  EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, RECIPIENT_EMAIL
  NAUKRI_QUERY, LINKEDIN_QUERY, WEB_QUERY, MAX_RESULTS
"""
import os
import re
import time
import smtplib
import html
import sqlite3
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Playwright used for JS-rendered pages; fallback to requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

load_dotenv()

# -------------------------
# Config / Environment
# -------------------------
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

NAUKRI_QUERY = os.getenv("NAUKRI_QUERY", "E-commerce Manager")
LINKEDIN_QUERY = os.getenv("LINKEDIN_QUERY", "Ecommerce Manager")
WEB_QUERY = os.getenv("WEB_QUERY", "E-commerce Manager jobs India")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "10"))

if not (EMAIL_USER and EMAIL_PASS and RECIPIENT_EMAIL):
    raise SystemExit("EMAIL_USER, EMAIL_PASS and RECIPIENT_EMAIL must be set in environment or .env")

# DB for seen jobs
DB_PATH = Path(__file__).parent / "seen_jobs.db"

# Target cities and normalization
TARGET_CITIES = [
    "pune", "mumbai", "navi mumbai", "thane", "mumbai metropolitan region",
    "bangalore", "bengaluru"
]

# Regex for contact extraction
EMAIL_REGEX = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
PHONE_REGEX = r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b"  # Indian 10-digit mobiles, allow optional +91

# -------------------------
# Utility functions
# -------------------------
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

def contains_target_city(text):
    if not text:
        return False
    txt = text.lower()
    return any(city in txt for city in TARGET_CITIES)

def extract_contact_info(text):
    if not text:
        return [], []
    emails = re.findall(EMAIL_REGEX, text)
    phones = re.findall(PHONE_REGEX, text)
    # normalize phones: remove spaces/hyphens, keep last 10 digits
    norm_phones = []
    for p in set(phones):
        p_clean = re.sub(r"[^\d]", "", p)
        if len(p_clean) > 10:
            p_clean = p_clean[-10:]
        if len(p_clean) == 10:
            norm_phones.append(p_clean)
    # unique
    return list(set(emails)), norm_phones

def make_search_urls():
    naukri_q = requests.utils.requote_uri(NAUKRI_QUERY)
    naukri_url = f"https://www.naukri.com/{naukri_q}-jobs"
    li_q = requests.utils.requote_uri(LINKEDIN_QUERY)
    linkedin_url = f"https://www.linkedin.com/jobs/search/?keywords={li_q}&location=India"
    web_q = requests.utils.requote_uri(WEB_QUERY)
    bing_url = f"https://www.bing.com/search?q={web_q}"
    return naukri_url, linkedin_url, bing_url

# -------------------------
# Fetching helpers
# -------------------------
def fetch_with_playwright(url, timeout=30000):
    """Return text content of url using Playwright (rendered)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, timeout=timeout)
            # wait a little for dynamic content (adjust if needed)
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            content = page.content()
            page.close()
            browser.close()
            return content
    except PWTimeoutError as e:
        print("Playwright timeout:", e)
        return None
    except Exception as e:
        print("Playwright error:", e)
        return None

def fetch_with_requests(url, headers=None, timeout=15):
    headers = headers or {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
        else:
            print(f"Requests fetch returned {r.status_code} for {url}")
            return None
    except Exception as e:
        print("Requests fetch error for", url, e)
        return None

def fetch_page_content(url):
    """Try Playwright first (preferred), fall back to requests."""
    content = fetch_with_playwright(url)
    if content:
        return content
    return fetch_with_requests(url)

# -------------------------
# Scrapers for each platform
# -------------------------
def extract_from_naukri(page_html):
    jobs = []
    if not page_html:
        return jobs
    soup = BeautifulSoup(page_html, "html.parser")
    # Naukri job tuples
    # multiple selectors because markup varies
    cards = soup.select("article.jobTuple") or soup.select("div.jobTuple") or soup.select("div.list")
    if not cards:
        # fallback: anchors to naukri view pages
        for a in soup.select("a"):
            href = a.get("href", "")
            if "naukri.com" in href and "/view/" in href:
                title = a.get_text(strip=True)
                jobs.append({"title": title, "company": "", "location": "", "link": href, "source": "Naukri"})
        return jobs

    for c in cards[:MAX_RESULTS * 2]:  # fetch a few more then filter later
        link_tag = c.select_one("a")
        href = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
        title_tag = c.select_one(".jobTitle") or (link_tag if link_tag else None)
        title = title_tag.get_text(strip=True) if title_tag else ""
        comp_tag = c.select_one(".companyName")
        company = comp_tag.get_text(strip=True) if comp_tag else ""
        loc_tag = c.select_one(".location")
        location = loc_tag.get_text(strip=True) if loc_tag else ""
        # Try to get short description/snippet if present
        desc_tag = c.select_one(".job-snippet") or c.select_one(".jobDescription") or c.select_one(".description")
        description = desc_tag.get_text(" ", strip=True) if desc_tag else ""
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "link": href,
            "source": "Naukri"
        })
    return jobs

def extract_from_linkedin(page_html):
    jobs = []
    if not page_html:
        return jobs
    soup = BeautifulSoup(page_html, "html.parser")
    cards = soup.select("ul.jobs-search__results-list li") or soup.select(".result-card.job-result-card")
    if not cards:
        # fallback: find job links that look like /jobs/view/
        for a in soup.select("a"):
            href = a.get("href", "")
            if "/jobs/view/" in href and "linkedin.com" in href:
                title = a.get_text(strip=True)
                jobs.append({"title": title, "company": "", "location": "", "description": "", "link": href, "source": "LinkedIn"})
        return jobs

    for c in cards[:MAX_RESULTS * 2]:
        a = c.select_one("a")
        href = a["href"] if a and a.has_attr("href") else None
        title_tag = c.select_one(".job-card-list__title") or c.select_one(".result-card__title") or a
        title = title_tag.get_text(strip=True) if title_tag else ""
        comp_tag = c.select_one(".job-card-container__company-name") or c.select_one(".result-card__subtitle")
        company = comp_tag.get_text(strip=True) if comp_tag else ""
        loc_tag = c.select_one(".job-card-container__metadata-item") or c.select_one(".job-result-card__location")
        location = loc_tag.get_text(strip=True) if loc_tag else ""
        # LinkedIn cards rarely contain a full description in the list page
        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "description": "",
            "link": href,
            "source": "LinkedIn"
        })
    return jobs

def extract_from_bing(page_html):
    jobs = []
    if not page_html:
        return jobs
    soup = BeautifulSoup(page_html, "html.parser")
    results = soup.select("li.b_algo")
    for r in results[:MAX_RESULTS * 2]:
        h2 = r.select_one("h2 a")
        if not h2:
            continue
        title = h2.get_text(strip=True)
        href = h2.get("href")
        snippet = (r.select_one(".b_paractl") or r.select_one(".b_caption p"))
        snippet = snippet.get_text(strip=True) if snippet else ""
        jobs.append({
            "title": title,
            "company": "",
            "location": "",
            "description": snippet,
            "link": href,
            "source": "Web/Bing"
        })
    return jobs

# -------------------------
# Job detail page fetch + contact extraction
# -------------------------
def enrich_job_with_details(job):
    """
    If job has a link, fetch the job page and attempt to extract a fuller description and contact info.
    Returns job dict updated in-place.
    """
    link = job.get("link")
    if not link:
        return job
    content = fetch_page_content(link)
    if not content:
        return job
    soup = BeautifulSoup(content, "html.parser")
    # Try to extract a fuller description from common selectors
    desc_selectors = [
        ".jd-desc", ".job-desc", ".job-description", "#jobDescriptionText",
        ".description", ".jobDescription", ".jobDesc", ".jdSec", ".job-desc-list"
    ]
    full_text = ""
    for sel in desc_selectors:
        el = soup.select_one(sel)
        if el:
            full_text = el.get_text(" ", strip=True)
            break
    if not full_text:
        # fallback: use big paragraphs and the whole page text (but limit length)
        all_text = soup.get_text(" ", strip=True)
        full_text = all_text[:2000]  # limit size for regex
    job["description"] = (job.get("description","") or "") + "\n\n" + full_text
    # Extract contact info from the combined description + page text
    combined = job["title"] + " " + job.get("location","") + " " + job["description"] + " " + (soup.get_text(" ", strip=True) or "")
    emails, phones = extract_contact_info(combined)
    job["emails"] = emails
    job["phones"] = phones
    return job

# -------------------------
# Email builder and sender
# -------------------------
def build_email_html(jobs):
    summary = {}
    for j in jobs:
        src = j.get("source", "Other")
        summary[src] = summary.get(src, 0) + 1

    html_parts = []
    html_parts.append(f"<h2>Daily E-commerce Job Report (Filtered: Pune / Mumbai / Bangalore)</h2>")
    html_parts.append(f"<p>Date: {time.strftime('%Y-%m-%d')}</p>")
    html_parts.append("<p><b>Summary:</b> " + ", ".join(f"{k}: {v}" for k,v in summary.items()) + "</p>")
    if not jobs:
        html_parts.append("<p>No new job listings matched your cities today.</p>")
    else:
        for job in jobs:
            title = html.escape(job.get("title","No title"))
            comp = html.escape(job.get("company",""))
            loc = html.escape(job.get("location",""))
            desc = html.escape((job.get("description") or "")[:600])  # trimmed
            link = job.get("link","")
            emails = job.get("emails",[]) or []
            phones = job.get("phones",[]) or []

            html_parts.append("<div style='margin-bottom:18px;padding:10px;border:1px solid #e1e1e1;border-radius:8px;'>")
            html_parts.append(f"<h3 style='margin:0'>{title}</h3>")
            if comp:
                html_parts.append(f"<p style='margin:4px 0'><b>Company:</b> {comp}</p>")
            if loc:
                html_parts.append(f"<p style='margin:4px 0'><b>Location:</b> {loc}</p>")
            html_parts.append(f"<p style='margin:6px 0'><b>Short description:</b> {desc}...</p>")
            if link:
                html_parts.append(f"<p style='margin:6px 0'><b>Apply / Job link:</b> <a href='{link}'>{link}</a></p>")
            if emails:
                html_parts.append(f"<p style='margin:6px 0'><b>Recruiter emails:</b> {', '.join(html.escape(e) for e in emails)}</p>")
            if phones:
                html_parts.append(f"<p style='margin:6px 0'><b>Recruiter phones:</b> {', '.join(html.escape(p) for p in phones)}</p>")
            html_parts.append(f"<p style='font-size:11px;color:#666;margin:6px 0'><i>Source: {job.get('source','')}</i></p>")
            html_parts.append("</div>")

    return "\n".join(html_parts)

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    s = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=60)
    s.ehlo()
    if EMAIL_PORT == 587:
        s.starttls()
    s.login(EMAIL_USER, EMAIL_PASS)
    s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    s.quit()

# -------------------------
# Main
# -------------------------
def main():
    conn = init_db()
    naukri_url, linkedin_url, bing_url = make_search_urls()
    urls = [naukri_url, linkedin_url, bing_url]

    print("Fetching search pages...")
    pages = {}
    # Use playwright for each search page, but tolerate failures
    for u in urls:
        print("Fetching:", u)
        html_text = fetch_page_content(u)
        pages[u] = html_text

    # Extract job listings
    all_jobs = []
    if naukri_url in pages and pages[naukri_url]:
        all_jobs += extract_from_naukri(pages[naukri_url])
    if linkedin_url in pages and pages[linkedin_url]:
        all_jobs += extract_from_linkedin(pages[linkedin_url])
    if bing_url in pages and pages[bing_url]:
        all_jobs += extract_from_bing(pages[bing_url])

    print(f"Found candidate listings: {len(all_jobs)} (before filtering/enrichment)")

    # Enrich each job by fetching detail page (to extract full description and contacts)
    enriched = []
    for j in all_jobs:
        # only attempt if there is a link, and avoid extremely long runs by limiting to MAX_RESULTS*2
        if j.get("link"):
            try:
                enrich_job_with_details(j)
            except Exception as e:
                print("Error enriching job", j.get("link"), e)
        # ensure fields exist
        j.setdefault("emails", [])
        j.setdefault("phones", [])
        j.setdefault("description", j.get("description",""))
        enriched.append(j)

    # Filter by target cities scanning title/location/description (Option B)
    filtered = []
    for job in enriched:
        combined = " ".join([job.get("title",""), job.get("location",""), job.get("description","")])
        if contains_target_city(combined):
            # deduplicate by link if seen
            link = job.get("link") or (job.get("title","")+job.get("company",""))
            if not is_seen(conn, link):
                filtered.append(job)
                mark_seen(conn, link)

    print(f"Filtered & new jobs to send: {len(filtered)}")

    # Build & send email
    subject = f"New Jobs Alert (Cities: Pune/Mumbai/Bangalore) — {time.strftime('%Y-%m-%d')}"
    html_body = build_email_html(filtered)
    try:
        send_email(subject, html_body)
        print("Email sent to", RECIPIENT_EMAIL)
    except Exception as e:
        print("Failed to send email:", e)

if __name__ == "__main__":
    main()
