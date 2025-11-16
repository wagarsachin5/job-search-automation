import requests
from bs4 import BeautifulSoup
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------------- CONFIG ----------------
TARGET_CITIES = [
    "pune", "pimpri", "pcmc", "pimpri chinchwad", 
    "hadapsar", "baner", "wakad", "hinge khurd", "kharadi"
]

ROLE_KEYWORDS = [
    "ecommerce", "e-commerce", "amazon", "flipkart",
    "marketplace", "catalog", "listing", "e commerce"
]

WALKIN_KEYWORDS = [
    "walk in", "walk-in", "walkin", "walkin interview", "walk in interview"
]

# Regex for contacts
EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
PHONE_REGEX = r"\b[6-9]\d{9}\b"

# Email config (use GitHub secrets)
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USER = "YOUR_EMAIL"
EMAIL_PASS = "YOUR_APP_PASSWORD"
RECIPIENT_EMAIL = "YOUR_EMAIL"

# Freshness filter: only jobs posted in last 1 day
ONE_DAY_AGO = datetime.now() - timedelta(days=1)

# ----------------------------------------


def extract_contact(text):
    emails = list(set(re.findall(EMAIL_REGEX, text)))
    phones = list(set(re.findall(PHONE_REGEX, text)))
    return emails, phones


def matches_filters(text):
    t = text.lower()
    return (
        any(city in t for city in TARGET_CITIES) and
        any(k in t for k in ROLE_KEYWORDS) and
        any(w in t for w in WALKIN_KEYWORDS)
    )


# ---------------- SCRAPERS ----------------

def scrape_naukri_walkins():
    print("Scraping Naukri Walk-ins...")
    url = "https://www.naukri.com/walkin-jobs"
    jobs = []

    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")
        cards = soup.find_all("article")

        for card in cards:
            text = card.get_text(" ", strip=True)

            # freshness check
            date_tag = card.find("span", {"class": "job-post-day"})
            if date_tag:
                posted = date_tag.text.strip().lower()
                if "day" in posted and "1" not in posted:
                    continue

            if matches_filters(text):
                title_tag = card.find("a")
                title = title_tag.text.strip() if title_tag else "NA"
                company = card.find("span", {"class": "comp-name"})
                company = company.text.strip() if company else "Not available"
                link = title_tag["href"] if title_tag else "#"

                emails, phones = extract_contact(text)

                jobs.append({
                    "title": title,
                    "company": company,
                    "description": text,
                    "link": link,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("Naukri Error:", e)

    return jobs



def scrape_foundit_walkins():
    print("Scraping Foundit Walk-ins...")
    url = "https://www.foundit.in/search/walkin-jobs"
    jobs = []

    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")
        cards = soup.find_all("div", class_="job-tuple")

        for card in cards:
            text = card.get_text(" ", strip=True).lower()

            # Foundit freshness check
            date_tag = card.find("span", class_="posted-date")
            if date_tag:
                if "1 day ago" not in date_tag.text.lower():
                    continue

            if matches_filters(text):
                title = card.find("h3").text.strip() if card.find("h3") else "NA"
                company = card.find("span", class_="company-name")
                company = company.text.strip() if company else "Not available"
                link = card.find("a")["href"]

                emails, phones = extract_contact(text)

                jobs.append({
                    "title": title,
                    "company": company,
                    "description": text,
                    "link": link,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("Foundit Error:", e)

    return jobs



def scrape_shine_walkins():
    print("Scraping Shine Walk-ins...")
    url = "https://www.shine.com/job-search/walkin-jobs"
    jobs = []

    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")
        cards = soup.find_all("div", class_="result-display__profile")

        for card in cards:
            text = card.get_text(" ", strip=True).lower()

            # Shine freshness check
            if "1 day ago" not in text and "posted today" not in text:
                continue

            if matches_filters(text):
                title = card.find("h2").text.strip() if card.find("h2") else "NA"
                company = card.find("span", class_="result-display__profile__company-name")
                company = company.text.strip() if company else "Not available"
                link = "https://www.shine.com" + card.find("a")["href"]

                emails, phones = extract_contact(text)

                jobs.append({
                    "title": title,
                    "company": company,
                    "description": text,
                    "link": link,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("Shine Error:", e)

    return jobs



def scrape_timesjobs_walkins():
    print("Scraping TimesJobs Walk-ins...")
    url = "https://www.timesjobs.com/candidate/job-search.html?searchType=personalizedSearch&txtKeywords=walkin&txtLocation=Pune"
    jobs = []

    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")
        cards = soup.find_all("li", class_="clearfix job-bx wht-shd-bx")

        for card in cards:
            text = card.get_text(" ", strip=True).lower()

            # freshness
            date_tag = card.find("span", class_="sim-posted")
            if date_tag and "1 day ago" not in date_tag.text.lower():
                continue

            if matches_filters(text):
                title = card.find("h2").text.strip()
                company = card.find("h3", class_="joblist-comp-name").text.strip()
                link = card.find("h2").a["href"]

                emails, phones = extract_contact(text)

                jobs.append({
                    "title": title,
                    "company": company,
                    "description": text,
                    "link": link,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("TimesJobs Error:", e)

    return jobs



def scrape_indeed_walkins():
    print("Scraping Indeed Walk-ins...")
    url = "https://in.indeed.com/jobs?q=walk-in+ecommerce&l=Pune"
    jobs = []

    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="cardOutline")

        for card in cards:
            text = card.get_text(" ", strip=True).lower()

            # freshness
            if "just posted" not in text and "1 day ago" not in text:
                continue

            if matches_filters(text):
                title = card.find("h2").text.strip()
                company = card.find("span", class_="companyName").text.strip()
                link = "https://in.indeed.com" + card.find("a")["href"]

                emails, phones = extract_contact(text)

                jobs.append({
                    "title": title,
                    "company": company,
                    "description": text,
                    "link": link,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("Indeed Error:", e)

    return jobs



def scrape_google_walkins():
    print("Scraping Google Search Walk-ins...")
    q = "Walk-in E-commerce jobs Pune"
    url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
    jobs = []

    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "html.parser")
        snippets = soup.find_all("div", class_="BNeawe s3v9rd AP7Wnd")

        for snip in snippets:
            text = snip.text.lower()

            if matches_filters(text):
                emails, phones = extract_contact(text)

                jobs.append({
                    "title": "Google Search Result",
                    "company": "Unknown",
                    "description": text,
                    "link": url,
                    "emails": emails,
                    "phones": phones
                })

    except Exception as e:
        print("Google Error:", e)

    return jobs



# ---------------- EMAIL SENDER ----------------

def send_email(jobs):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = "Daily Walk-In E-commerce Jobs (Pune) – Last 1 Day Only"

    html = "<h2>Today's Walk-In E-commerce Jobs (Pune)</h2>"

    if not jobs:
        html += "<p>No walk-in jobs found in the last 1 day.</p>"
    else:
        for job in jobs:
            html += f"""
            <div style='border:1px solid #ddd;padding:12px;margin-bottom:14px;'>
                <h3>{job['title']}</h3>
                <p><b>Company:</b> {job['company']}</p>
                <p>{job['description'][:250]}...</p>
                <p><b>Link:</b> <a href="{job['link']}">Open</a></p>
            """

            if job["emails"]:
                html += f"<p><b>Emails:</b> {', '.join(job['emails'])}</p>"
            if job["phones"]:
                html += f"<p><b>Phones:</b> {', '.join(job['phones'])}</p>"

            html += "</div>"

    msg.attach(MIMEText(html, "html"))

    s = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
    s.starttls()
    s.login(EMAIL_USER, EMAIL_PASS)
    s.sendmail(EMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    s.quit()



# ---------------- MAIN ----------------

def main():
    all_jobs = []
    all_jobs += scrape_naukri_walkins()
    all_jobs += scrape_foundit_walkins()
    all_jobs += scrape_shine_walkins()
    all_jobs += scrape_timesjobs_walkins()
    all_jobs += scrape_indeed_walkins()
    all_jobs += scrape_google_walkins()

    send_email(all_jobs)


if __name__ == "__main__":
    main()
