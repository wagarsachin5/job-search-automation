import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------
# SETTINGS
# ----------------------------------------------------
CITY = "Pune"
SEARCH_ROLE = "Ecommerce walkin"

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

FRESHNESS_DAYS = 1

# ----------------------------------------------------
# UTILITIES
# ----------------------------------------------------
def fetch(url, headers=None):
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def is_recent(date_string):
    today = datetime.now()
    try:
        if "Today" in date_string or "today" in date_string:
            return True
        if "Yesterday" in date_string.lower():
            return False
        digits = [int(s) for s in date_string.split() if s.isdigit()]
        if digits:
            days = digits[0]
            return days <= FRESHNESS_DAYS
    except:
        return False
    return False

# ----------------------------------------------------
# SCRAPERS
# ----------------------------------------------------

def scrape_naukri():
    print("Scraping Naukri Walk-ins...")

    url = f"https://www.naukri.com/walkin-jobs?keyword={SEARCH_ROLE}&location={CITY}"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for job in soup.select("article.jobTuple"):        
        title = job.select_one("a.title")
        company = job.select_one("a.subTitle")
        location = job.select_one("li.location")
        posted = job.select_one("div.type")

        if not title:
            continue

        date_text = posted.get_text(strip=True) if posted else ""
        if not is_recent(date_text):
            continue

        results.append({
            "source": "Naukri",
            "title": title.get_text(strip=True),
            "company": company.get_text(strip=True) if company else "",
            "location": location.get_text(strip=True) if location else "",
            "date": date_text,
            "link": title.get("href")
        })

    return results


# ----------------------------------------------------

def scrape_foundit():
    print("Scraping Foundit Walk-ins...")

    url = f"https://www.foundit.in/srp/results?query={SEARCH_ROLE}&locations={CITY}&sort=date"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for job in soup.select("div.cardContainer"):
        title = job.select_one("h3.title a")
        company = job.select_one("span.company-name")
        location = job.select_one("div.loc")
        posted = job.select_one("span.freshness")

        if not title:
            continue

        date_text = posted.get_text(strip=True) if posted else ""
        if not is_recent(date_text):
            continue

        results.append({
            "source": "Foundit",
            "title": title.get_text(strip=True),
            "company": company.get_text(strip=True) if company else "",
            "location": location.get_text(strip=True) if location else "",
            "date": date_text,
            "link": title.get("href")
        })

    return results


# ----------------------------------------------------

def scrape_indeed():
    print("Scraping Indeed Walk-ins...")

    url = f"https://in.indeed.com/jobs?q=walkin+ecommerce&l={CITY}&sort=date"
    html = fetch(url, headers={"User-Agent": "Mozilla/5.0"})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for job in soup.select("div.slider_container"):
        title = job.select_one("h2.jobTitle")
        company = job.select_one("span.companyName")
        location = job.select_one("div.companyLocation")
        posted = job.select_one("span.date")

        if not title:
            continue

        date_text = posted.get_text(strip=True) if posted else ""
        if not is_recent(date_text):
            continue

        link_el = job.select_one("a")
        link = "https://in.indeed.com" + link_el.get("href") if link_el else ""

        results.append({
            "source": "Indeed",
            "title": title.get_text(strip=True),
            "company": company.get_text(strip=True) if company else "",
            "location": location.get_text(strip=True) if location else "",
            "date": date_text,
            "link": link
        })

    return results


# ----------------------------------------------------

def scrape_shine():
    print("Scraping Shine Walk-ins...")

    url = f"https://www.shine.com/job-search/walkin-jobs-in-{CITY.lower().replace(' ', '-')}/"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for job in soup.select("div.jobCard_searchResult"):
        title = job.select_one("a.job_title")
        company = job.select_one("div.jobCard_jobName__z3xJq")
        location = job.select_one("span.jobCard_location__N0GmR")
        posted = job.select_one("span.jobCard_date__jjUrb")

        if not title:
            continue

        date_text = posted.get_text(strip=True) if posted else ""
        if not is_recent(date_text):
            continue

        results.append({
            "source": "Shine",
            "title": title.get_text(strip=True),
            "company": company.get_text(strip=True) if company else "",
            "location": location.get_text(strip=True) if location else "",
            "date": date_text,
            "link": "https://www.shine.com" + title.get("href")
        })

    return results


# ----------------------------------------------------
# EMAIL SENDER
# ----------------------------------------------------
def send_email(jobs):
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = f"Pune Walk-in Jobs – {datetime.now().strftime('%d %b %Y')}"

    if not jobs:
        html = "<h2>No new walk-in jobs found today.</h2>"
    else:
        rows = ""
        for j in jobs:
            rows += f"""
            <tr>
                <td>{j['source']}</td>
                <td><a href='{j['link']}'>{j['title']}</a></td>
                <td>{j['company']}</td>
                <td>{j['location']}</td>
                <td>{j['date']}</td>
            </tr>
            """

        html = f"""
        <h2>Walk-in Jobs for Pune</h2>
        <table border='1' cellpadding='6' cellspacing='0'>
        <tr><th>Source</th><th>Title</th><th>Company</th><th>Location</th><th>Date</th></tr>
        {rows}
        </table>
        """

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT) as s:
            s.login(EMAIL_USER, EMAIL_PASS)
            s.send_message(msg)
        print("Email sent successfully!")
    except Exception as e:
        print(f"Email sending failed: {e}")


# ----------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------
def main():
    all_jobs = []

    all_jobs.extend(scrape_naukri())
    all_jobs.extend(scrape_foundit())
    all_jobs.extend(scrape_indeed())
    all_jobs.extend(scrape_shine())

    send_email(all_jobs)


if __name__ == "__main__":
    main()
