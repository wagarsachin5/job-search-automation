import os
import time
import smtplib
import html
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

NAUKRI_QUERY = os.getenv("NAUKRI_QUERY", "E-commerce Manager")
LINKEDIN_QUERY = os.getenv("LINKEDIN_QUERY", "Ecommerce Manager")
WEB_QUERY = os.getenv("WEB_QUERY", "E-commerce Manager jobs India")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "10"))

# Database to store seen links
DB_PATH = Path(__file__).parent / "seen_jobs.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            link TEXT PRIMARY KEY,
            first_seen TIMESTAMP
        )
    """)
    conn.commit()
    return conn

def mark_seen(conn, link):
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO seen (link, first_seen) VALUES (?, ?)", (link, int(time.time())))
    conn.commit()

def is_seen(conn, link):
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen WHERE link = ?", (link,))
    return c.fetchone() is not None

def make_search_urls():
    naukri_q = requests.utils.requote_uri(NAUKRI_QUERY)
    naukri_url = f"https://www.naukri.com/{naukri_q}-jobs"
    li_q = requests.utils.requote_uri(LINKEDIN_QUERY)
    linkedin_url = f"https://www.linkedin.com/jobs/search/?keywords={li_q}&location=India"
    web_q = requests.utils.requote_uri(WEB_QUERY)
    bing_url = f"https://www.bing.com/search?q={web_q}"
    return naukri_url, linkedin_url, bing_url

def extract_from_naukri(page_content):
    jobs = []
    soup = BeautifulSoup(page_content, "html.parser")
    cards = soup.select("article.jobTuple, .jobTuple")
    for c in cards[:MAX_RESULTS]:
        link_tag = c.select_one("a")
        href = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
        title = c.select_one(".jobTitle")
        title = title.get_text(strip=True) if title else (link_tag.get_text(strip=True) if link_tag else "")
        comp = c.select_one(".companyName")
        comp = comp.get_text(strip=True) if comp else ""
        loc = c.select_one(".location")
        loc = loc.get_text(strip=True) if loc else ""
        jobs.append({"title": title, "company": comp, "location": loc, "link": href, "source": "Naukri"})
    return jobs

def extract_from_linkedin(page_content):
    jobs = []
    soup = BeautifulSoup(page_content, "html.parser")
    cards = soup.select("ul.jobs-search__results-list li")
    for c in cards[:MAX_RESULTS]:
        a = c.select_one("a")
        href = a["href"] if a and a.has_attr("href") else None
        title_tag = c.select_one(".job-card-list__title")
        title = title_tag.get_text(strip=True) if title_tag else (a.get_text(strip=True) if a else "")
        comp_tag = c.select_one(".job-card-container__company-name")
        comp = comp_tag.get_text(strip=True) if comp_tag else ""
        loc_tag = c.select_one(".job-card-container__metadata-item")
        loc = loc_tag.get_text(strip=True) if loc_tag else ""
        jobs.append({"title": title, "company": comp, "location": loc, "link": href, "source": "LinkedIn"})
    return jobs

def extract_from_bing(page_content):
    jobs = []
    soup = BeautifulSoup(page_content, "html.parser")
    results = soup.select("li.b_algo")
    for r in results[:MAX_RESULTS]:
        h2 = r.select_one("h2 a")
        if not h2:
            continue
        title = h2.get_text(strip=True)
        href = h2.get("href")
        snippet = (r.select_one(".b_paractl") or r.select_one(".b_caption p"))
        snippet = snippet.get_text(strip=True) if snippet else ""
        jobs.append({"title": title, "company": "", "location": "", "link": href, "summary": snippet, "source": "Web/Bing"})
    return jobs

def fetch_pages(urls):
    pages = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            for url in urls:
                page = context.new_page()
                page.goto(url, timeout=30000)
                time.sleep(1.5)
                pages[url] = page.content()
                page.close()
            browser.close()
    except Exception as e:
        print("Playwright failed:", e, "- falling back to requests")
        for url in urls:
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                pages[url] = r.text
            except Exception as e2:
                print("Request failed for", url, ":", e2)
    return pages

def build_email_html(new_jobs):
    parts = []
    parts.append(f"<h2>New job listings for “{html.escape(NAUKRI_QUERY)} / {html.escape(LINKEDIN_QUERY)}”</h2>")
    parts.append(f"<p>Date: {time.strftime('%Y-%m-%d')}</p>")
    if not new_jobs:
        parts.append("<p>No new job listings found today.</p>")
    else:
        parts.append("<ul>")
        for job in new_jobs:
            parts.append("<li>")
            parts.append(f"<strong>{html.escape(job['title'])}</strong>")
            if job.get("company"):
                parts.append(f" — {html.escape(job['company'])}")
            if job.get("location"):
                parts.append(f" ({html.escape(job['location'])})")
            parts.append("<br>")
            if job.get("link"):
                parts.append(f"<a href=\"{job['link']}\">{job['link']}</a><br>")
            if job.get("summary"):
                parts.append(f"<small>{html.escape(job['summary'])}</small><br>")
            parts.append(f"<em>Source: {job.get('source')}</em>")
            parts.append("</li><br>")
        parts.append("</ul>")
    return "\n".join(parts)

def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
        s.ehlo()
        if EMAIL_PORT == 587:
            s.starttls()
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())

def main():
    conn = init_db()
    urls = make_search_urls()
    pages = fetch_pages(urls)

    all_jobs = []
    if urls[0] in pages:
        all_jobs += extract_from_naukri(pages[urls[0]])
    if urls[1] in pages:
        all_jobs += extract_from_linkedin(pages[urls[1]])
    if urls[2] in pages:
        all_jobs += extract_from_bing(pages[urls[2]])

    new_jobs = []
    for job in all_jobs:
        link = job.get("link")
        if not link:
            continue
        if not is_seen(conn, link):
            mark_seen(conn, link)
            new_jobs.append(job)

    html_body = build_email_html(new_jobs)
    subject = f"New Jobs Alert: {NAUKRI_QUERY} / {LINKEDIN_QUERY} — {time.strftime('%Y-%m-%d')}"
    send_email(subject, html_body)
    print(f"Found {len(new_jobs)} new jobs; email sent to {RECIPIENT_EMAIL}")

if __name__ == "__main__":
    main()
