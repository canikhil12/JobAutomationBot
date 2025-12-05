import os
import time
import datetime as dt
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

# ----------------------------------------------------
# Load environment variables (reuse same .env + driver)
# ----------------------------------------------------
load_dotenv()

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")

# ----------------------------------------------------
# Google Sheets helpers (same style as recruiter_bot)
# ----------------------------------------------------
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "service_account.json", scope
    )
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1


def append_recruiter_row(sheet, recruiter):
    """
    recruiter = {
      "recruiter_name": ...,
      "job_title": ...,
      "company": ...,
      "linkedin_url": ...,
      "notes": ...
    }
    """
    # We assume your header is:
    # recruiter_name | job_title | company | linkedin_url |
    # status | message1_sent | message2_sent | last_contacted | notes

    row = [
        recruiter.get("recruiter_name", ""),
        recruiter.get("job_title", ""),
        recruiter.get("company", ""),
        recruiter.get("linkedin_url", ""),
        "pending",   # status
        "",          # message1_sent
        "",          # message2_sent
        "",          # last_contacted
        recruiter.get("notes", ""),
    ]

    sheet.append_row(row, value_input_option="RAW")
    print(f"✅ Added to sheet: {row[0]} | {row[1]} | {row[2]}")


# ----------------------------------------------------
# Selenium driver (reusing your working config)
# ----------------------------------------------------
def init_driver():
    options = webdriver.ChromeOptions()

    # Use same profile or a clean one
    options.add_argument("user-data-dir=" + os.path.expanduser("~") + "/.jobbot-profile")
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    service = Service(CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


# ----------------------------------------------------
# Scrape helpers
# ----------------------------------------------------
def scrape_jobright_page(driver, url):
    """
    Given a JobRight job page, try to extract:
      - job title
      - company
      - recruiter(s) with LinkedIn profile links
    """
    print(f"\n🌐 Opening JobRight job: {url}")
    driver.get(url)
    time.sleep(5)

    recruiters = []

    # Try to get job title (best-effort, may need tweaks)
    try:
        # Common pattern: main job title as h1 (adjust if needed)
        job_title_el = driver.find_element(By.XPATH, "//h1")
        job_title = job_title_el.text.strip()
    except NoSuchElementException:
        job_title = "Data Analyst"

    # Try to get company name
    company = ""
    try:
        # Often company name appears near the job title
        company_el = driver.find_element(
            By.XPATH,
            "//h1/ancestor::div[1]//a[contains(@href, 'company')] | //h1/ancestor::div[1]//span"
        )
        company = company_el.text.strip()
    except NoSuchElementException:
        company = ""

    # Find any LinkedIn recruiter profile links on page
    # Many JobRight pages have "View recruiter" or "View on LinkedIn" links
    profile_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'linkedin.com/in')]")

    if not profile_links:
        print("⚠ No LinkedIn recruiter links found on this JobRight page.")
        return []

    for link in profile_links:
        href = link.get_attribute("href") or ""
        name_text = link.text.strip()

        if not href.startswith("https://www.linkedin.com/in/"):
            continue

        recruiter_name = name_text or "Recruiter"
        recruiters.append({
            "recruiter_name": recruiter_name,
            "job_title": job_title or "Recruiter",
            "company": company,
            "linkedin_url": href.split("?")[0],  # clean query params
            "notes": "Auto-filled from JobRight"
        })

    print(f"🔎 Found {len(recruiters)} recruiter profiles on JobRight page.")
    return recruiters


def scrape_linkedin_job_page(driver, url):
    """
    Given a LinkedIn job URL, try to extract:
      - job title
      - company
      - 'Posted by' / 'Meet the hiring team' recruiter profiles
    """
    print(f"\n🌐 Opening LinkedIn job: {url}")
    driver.get(url)
    time.sleep(6)

    recruiters = []

    # Job title
    try:
        job_title_el = driver.find_element(
            By.XPATH,
            "//h1[contains(@class,'jobs-unified-top-card__job-title')] | //h1"
        )
        job_title = job_title_el.text.strip()
    except NoSuchElementException:
        job_title = "Data Analyst"

    # Company
    company = ""
    try:
        company_el = driver.find_element(
            By.XPATH,
            "//a[contains(@class,'jobs-unified-top-card__company-name')] | //span[contains(@class,'jobs-unified-top-card__company-name')]"
        )
        company = company_el.text.strip()
    except NoSuchElementException:
        company = ""

    # Hiring team / recruiter profiles: often `linkedin.com/in` links in side panel
    profile_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'linkedin.com/in')]")

    if not profile_links:
        print("⚠ No recruiter profiles found on LinkedIn job page.")
        return []

    for link in profile_links:
        href = link.get_attribute("href") or ""
        name_text = link.text.strip()

        if not href.startswith("https://www.linkedin.com/in/"):
            continue

        recruiter_name = name_text or "Hiring Manager"
        recruiters.append({
            "recruiter_name": recruiter_name,
            "job_title": job_title,
            "company": company,
            "linkedin_url": href.split("?")[0],
            "notes": "Auto-filled from LinkedIn job"
        })

    print(f"🔎 Found {len(recruiters)} recruiter profiles on LinkedIn job page.")
    return recruiters


# ----------------------------------------------------
# Main autofill flow
# ----------------------------------------------------
def main():
    sheet = get_sheet()
    driver = init_driver()

    print("📥 Paste JobRight / LinkedIn job URLs below (one per line).")
    print("   Press ENTER on an empty line when done.\n")

    urls = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        urls.append(line)

    if not urls:
        print("⚠ No URLs entered. Exiting.")
        driver.quit()
        return

    total_added = 0

    for url in urls:
        try:
            if "jobright.ai" in url:
                recs = scrape_jobright_page(driver, url)
            elif "linkedin.com/jobs" in url:
                recs = scrape_linkedin_job_page(driver, url)
            else:
                print(f"⚠ Unsupported URL type, skipping: {url}")
                continue

            for r in recs:
                append_recruiter_row(sheet, r)
                total_added += 1

        except Exception as e:
            print(f"❌ Error processing {url}: {e}")

    driver.quit()
    print(f"\n🎉 Done. Total recruiters added to sheet: {total_added}")


if __name__ == "__main__":
    main()