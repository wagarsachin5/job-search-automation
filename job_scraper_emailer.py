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
    if not page_html_
