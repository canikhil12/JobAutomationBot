import os
import time
import datetime as dt
import random

from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

import streamlit as st

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ----------------------------------------------------
# Load environment
# ----------------------------------------------------
load_dotenv()

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")
DAILY_MAX_FIRST = int(os.getenv("DAILY_MAX_FIRST_MESSAGES", "20"))
DAILY_MAX_FOLLOWUPS = int(os.getenv("DAILY_MAX_FOLLOWUPS", "10"))

# ----------------------------------------------------
# Message templates (full + short)
# ----------------------------------------------------
FIRST_MESSAGE_FULL = """Hi {name},

I came across your profile while exploring opportunities for a Data Analyst role at {company}. I’ve been working with SQL, Python, ETL pipelines, dashboard reporting, and analytics in both banking and healthcare (TD Bank, Availity).

If you're hiring or can point me to the right person on your team, I’d appreciate a quick review of my background.

Thanks,
Akhil
"""

# < 300 chars – safe for Connect → Add a note
FIRST_MESSAGE_SHORT = (
    "Hi {name}, I’m exploring Data Analyst roles at {company}. I work with SQL, Python, "
    "ETL, and dashboards in banking and healthcare. Would appreciate connecting and any "
    "guidance on analytics opportunities. Thanks, Akhil"
)

FOLLOWUP_MESSAGE_FULL = """Hi {name},

Just following up on my previous note regarding opportunities at {company}.
If there's someone else on your team who handles analytics hiring, I’d be grateful if you could point me to them.

Thanks again,
Akhil
"""

FOLLOWUP_MESSAGE_SHORT = (
    "Hi {name}, just following up on my earlier note about analytics roles at {company}. "
    "If someone else handles data hiring, I’d really appreciate it if you could point me "
    "their way. Thanks, Akhil"
)


def shorten_for_note(text: str, limit: int = 300) -> str:
    """
    Safety net for notes. If something ever exceeds 300 characters,
    trim it and add '...'.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


# ----------------------------------------------------
# Helpers: Logging
# ----------------------------------------------------
def log(msg: str):
    """Log to Streamlit and terminal."""
    try:
        st.write(msg)
    except Exception:
        # In case script is run outside streamlit
        pass
    print(msg)


# ----------------------------------------------------
# Google Sheets helpers
# ----------------------------------------------------
def get_sheet():
    if not GOOGLE_SHEET_NAME:
        raise RuntimeError("GOOGLE_SHEET_NAME is not set in .env")

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "service_account.json", scope
    )
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1


def get_rows(sheet):
    return sheet.get_all_records()


def update_row(sheet, index, **kwargs):
    row_number = index + 2  # header is row 1
    header = sheet.row_values(1)

    for col_name, value in kwargs.items():
        if col_name in header:
            col_index = header.index(col_name) + 1
            sheet.update_cell(row_number, col_index, value)


def append_recruiter_row(sheet, data):
    """
    Expect sheet header:
    recruiter_name | job_title | company | linkedin_url |
    status | message1_sent | message2_sent | last_contacted | notes | job_url
    """
    row = [
        data.get("recruiter_name", ""),
        data.get("job_title", ""),
        data.get("company", ""),
        data.get("linkedin_url", ""),
        "pending",  # status
        "",  # message1_sent
        "",  # message2_sent
        "",  # last_contacted
        data.get("notes", ""),
        data.get("job_url", ""),
    ]
    sheet.append_row(row, value_input_option="RAW")
    log(f"✔ Added recruiter: {row[0]} | {row[2]} | {row[9]}")


# ----------------------------------------------------
# Selenium driver
# ----------------------------------------------------
def init_driver():
    if not CHROME_DRIVER_PATH:
        raise RuntimeError("CHROME_DRIVER_PATH is not set in .env")

    options = webdriver.ChromeOptions()
    # Use same profile as before so you're logged in
    profile_path = os.path.expanduser("~/jobbot-chrome-profile")
    options.add_argument(f"user-data-dir={profile_path}")
    options.binary_location = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )

    service = Service(CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def login_jobright(driver):
    JOBRIGHT_EMAIL = os.getenv("JOBRIGHT_EMAIL")
    JOBRIGHT_PASSWORD = os.getenv("JOBRIGHT_PASSWORD")

    if not JOBRIGHT_EMAIL or not JOBRIGHT_PASSWORD:
        print("❌ Missing JOBRIGHT_EMAIL or JOBRIGHT_PASSWORD in .env")
        return False

    print("🔍 Checking if JobRight login page is visible...")

    time.sleep(3)

    try:
        email_input = driver.find_element(By.XPATH, "//input[@type='email']")
        password_input = driver.find_element(By.XPATH, "//input[@type='password']")
        login_button = driver.find_element(
            By.XPATH, "//button[contains(., 'Log in') or contains(., 'Login')]"
        )

        print("🟦 JobRight login form detected. Logging in...")

        email_input.clear()
        email_input.send_keys(JOBRIGHT_EMAIL)
        time.sleep(1)

        password_input.clear()
        password_input.send_keys(JOBRIGHT_PASSWORD)
        time.sleep(1)

        driver.execute_script("arguments[0].click();", login_button)
        time.sleep(5)

        print("✅ JobRight login successful!")
        return True

    except Exception:
        print("ℹ No login form found — maybe already logged in.")
        return False


def scroll_to_bottom(driver):
    """Scroll JobRight applied page until all jobs loaded."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


# ----------------------------------------------------
# 1) Collect applied JobRight jobs
# ----------------------------------------------------
def scroll_jobright_joblist(driver):
    """
    Scroll inside the JobRight applied jobs list until all jobs are loaded.

    We auto-detect a scrollable <div> using JS (overflow-y: auto/scroll),
    then keep scrolling while the number of job cards keeps increasing.
    """
    log("📜 Locating scrollable job list container...")

    # Use JavaScript to find the first scrollable DIV on the page
    scroll_box = driver.execute_script("""
        const divs = Array.from(document.querySelectorAll('div'));
        for (const d of divs) {
            const style = window.getComputedStyle(d);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                d.scrollHeight > d.clientHeight + 50 &&
                d.clientHeight > 0) {
                return d;
            }
        }
        return null;
    """)

    if not scroll_box:
        log("❌ FAILED to locate job list scroll container via JS. Cannot deep-scroll.")
        return

    log("✅ Job list scroll container located. Beginning deep scroll...")

    last_count = 0
    unchanged = 0
    max_loops = 80          # safety cap – enough for 800+ jobs

    for i in range(max_loops):
        # Count how many job cards are currently rendered
        cards = driver.find_elements(
            By.CSS_SELECTOR,
            "div.job-card-flag-classname.index_job-card__AsPKC"
        )
        cur_count = len(cards)
        log(f"   • Loop {i+1}: currently {cur_count} job cards visible")

        if cur_count == last_count:
            unchanged += 1
        else:
            unchanged = 0
            last_count = cur_count

        # If card count hasn't changed for several iterations, we're at the end
        if unchanged >= 5:
            break

        # Scroll the inner container to the bottom
        try:
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight;",
                scroll_box
            )
        except Exception:
            # Fallback: scroll whole window as a backup
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(2.5)

    log("🎉 Finished deep scrolling — applied jobs list fully loaded.")


def collect_applied_jobright_urls():
    """
    Loads ALL applied jobs from JobRight (deep scroll),
    extracts unique job URLs, and saves them to jobright_applied_urls.txt
    without duplicates.
    """
    driver = init_driver()

    try:
        log("🌐 Opening JobRight applied jobs page...")
        driver.get("https://jobright.ai/jobs/applied")
        time.sleep(8)

        login_jobright(driver)
        time.sleep(3)

        log("📜 Deep-scrolling the applied jobs list...")
        scroll_jobright_joblist(driver)

        # After scrolling, extract all job cards
        cards = driver.find_elements(
            By.CSS_SELECTOR,
            "div.job-card-flag-classname.index_job-card__AsPKC"
        )

        log(f"🧾 Found {len(cards)} job cards on screen after deep scrolling.")

        # Extract JobRight job IDs and convert to URLs
        urls = []
        for card in cards:
            job_id = card.get_attribute("id")
            if job_id:
                urls.append(f"https://jobright.ai/jobs/info/{job_id}")

        # De-duplicate
        urls = list(set(urls))
        log(f"🔎 Extracted {len(urls)} unique job URLs from the applied list.")

        # Load previously saved URLs (persistent across runs)
        file_path = "jobright_applied_urls.txt"
        old_urls = set()

        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                old_urls = {line.strip() for line in f if line.strip()}

        log(f"📦 Previously saved URLs: {len(old_urls)}")

        # New URLs only
        new_urls = [u for u in urls if u not in old_urls]
        log(f"✨ New URLs discovered this run: {len(new_urls)}")

        if new_urls:
            with open(file_path, "a") as f:
                for u in new_urls:
                    f.write(u + "\n")
            log("💾 Appended new URLs to jobright_applied_urls.txt")
        else:
            log("✔ No new URLs. All applied jobs are already saved.")

        return urls

    finally:
        driver.quit()


# ----------------------------------------------------
# 2) Extract recruiters from each applied job
# ----------------------------------------------------
def extract_recruiters_from_jobright(driver, url):
    log(f"\n🌐 Opening job: {url}")
    driver.get(url)
    time.sleep(4)

    recruiters = []

    # Job title
    try:
        job_title = driver.find_element(By.XPATH, "//h1").text.strip()
    except NoSuchElementException:
        job_title = "Data Analyst"

    # Company
    company = ""
    try:
        company_el = driver.find_element(By.XPATH, "//h1/ancestor::div[1]//a")
        company = company_el.text.strip()
    except NoSuchElementException:
        company = ""

    links = driver.find_elements(By.XPATH, "//a[contains(@href, 'linkedin.com/in')]")

    if not links:
        log("⚠ No recruiter profiles found on this job page.")
        return []

    for link in links:
        href = link.get_attribute("href") or ""
        name = link.text.strip()

        if "/in/" not in href:
            continue

        recruiter_name = name if name else "Hiring Team"

        recruiters.append(
            {
                "recruiter_name": recruiter_name,
                "job_title": job_title,
                "company": company,
                "linkedin_url": href.split("?")[0],
                "notes": "Auto-filled from applied job",
            }
        )

    log(f"🔎 Found {len(recruiters)} recruiter profiles on this job.")
    return recruiters


def run_extract_recruiters():
    file_path = "jobright_applied_urls.txt"
    if not os.path.exists(file_path):
        log("❌ jobright_applied_urls.txt NOT FOUND! Run [1] Collect applied jobs first.")
        return

    with open(file_path, "r") as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        log("❌ No URLs in jobright_applied_urls.txt")
        return

    log(f"📄 Loaded {len(urls)} applied job URLs.")
    sheet = get_sheet()

    existing_records = get_rows(sheet)

    existing_job_urls = set()
    existing_linkedin_urls = set()

    for r in existing_records:
        ju = str(r.get("job_url", "")).strip()
        lu = str(r.get("linkedin_url", "")).strip()
        if ju:
            existing_job_urls.add(ju)
        if lu:
            existing_linkedin_urls.add(lu)

    log(f"📌 Existing job_url count in sheet: {len(existing_job_urls)}")
    log(f"📌 Existing linkedin_url count in sheet: {len(existing_linkedin_urls)}")

    driver = init_driver()
    total = 0
    try:
        for url in urls:
            if url in existing_job_urls:
                log(f"⏩ Skipping already-processed JobRight URL: {url}")
                continue

            try:
                recs = extract_recruiters_from_jobright(driver, url)
                for rec in recs:
                    linkedin_url = rec.get("linkedin_url", "").strip()
                    if not linkedin_url:
                        continue

                    if linkedin_url in existing_linkedin_urls:
                        log(f"⏩ Skipping duplicate LinkedIn: {linkedin_url}")
                        continue

                    rec["job_url"] = url

                    append_recruiter_row(sheet, rec)
                    total += 1

                    existing_linkedin_urls.add(linkedin_url)
                    existing_job_urls.add(url)
            except Exception as e:
                log(f"❌ Error processing {url}: {e}")
    finally:
        driver.quit()

    log(f"\n🎉 DONE! Added {total} recruiters to your Google Sheet (no duplicates).")


# ----------------------------------------------------
# SAFETY HELPERS FOR LINKEDIN OUTREACH
# ----------------------------------------------------
def normalize_name(name: str) -> str:
    """
    Normalize names for comparison: lowercase, strip, drop extra spaces.
    """
    return " ".join(name.lower().split())


def verify_profile_loaded(driver, expected_name: str, timeout: int = 8) -> bool:
    """
    Safety check: ensure the LinkedIn profile page we just opened
    actually belongs to (or at least visibly contains) the expected_name.
    """
    if not expected_name:
        return True  # nothing to verify

    expected_norm = normalize_name(expected_name)
    end_time = time.time() + timeout

    while time.time() < end_time:
        try:
            # Try grabbing the top profile name (h1 or similar)
            possible_name_elements = driver.find_elements(
                By.XPATH,
                "//h1 | //div[contains(@class,'pv-text-details__left-panel')]//h1",
            )
            for el in possible_name_elements:
                if not el.is_displayed():
                    continue
                actual = normalize_name(el.text)
                if expected_norm and expected_norm in actual:
                    log(f"✅ Verified profile header matches expected name: {expected_name}")
                    return True
        except Exception:
            pass

        # Fallback: body text scan (less strict)
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            if expected_norm in body_text:
                log(f"✅ Verified profile contains expected name: {expected_name}")
                return True
        except Exception:
            pass

        time.sleep(1)

    log(f"❌ SAFETY BLOCKED: Could not verify correct profile for {expected_name}")
    return False


def verify_message_modal_recipient(driver, expected_name: str) -> bool:
    """
    Additional safety check: verify that the currently open message / note modal
    appears to belong to the expected_name (in the header / title).
    This is a best-effort check and errs on the side of NOT sending.
    """
    if not expected_name:
        return True

    expected_norm = normalize_name(expected_name)

    try:
        # Common selectors for LinkedIn message headers / titles
        header_candidates = driver.find_elements(
            By.XPATH,
            "//h2[contains(@class,'msg-overlay-bubble-header__title')]"
            " | //h2[contains(@class,'artdeco-modal__header')]"
            " | //h3[contains(@class,'msg-entity-lockup__entity-name')]"
            " | //div[contains(@class,'msg-thread__link')]/span"
        )
        for h in header_candidates:
            if not h.is_displayed():
                continue
            actual = normalize_name(h.text)
            if expected_norm and expected_norm in actual:
                log(f"✅ Verified message modal recipient matches {expected_name}")
                return True
    except Exception:
        pass

    log(f"❌ SAFETY BLOCKED: Message modal recipient does not match {expected_name}")
    return False


# ----------------------------------------------------
# 3 & 4) LinkedIn messaging bot (first + follow-up)
# ----------------------------------------------------
def random_delay():
    time.sleep(random.uniform(5, 10))


def open_message_or_connect(driver):
    """
    Prefer FREE outreach:

      1) If a Connect button exists:
           - click Connect
           - click "Add a note" (if present) OR use editor if it appears directly
           - return "note"

      2) If NO Connect, but a real Message button exists (1st-degree):
           - click Message -> open chat
           - return "message"

      3) If only Follow exists, or InMail/paywall:
           - click Follow (optional)
           - return "follow_only"

      4) If nothing usable:
           - return None
    """

    # ---------- 1. TRY CONNECT FIRST ----------
    try:
        connect_candidates = driver.find_elements(
            By.XPATH,
            "//button["
            "   .//span[normalize-space(text())='Connect']"
            "   or contains(@aria-label,'Invite')"
            "   or contains(@aria-label,'connect')"
            "   or .//use[contains(@href,'connect-small')]"
            "]",
        )

        connect_btn = None
        for b in connect_candidates:
            try:
                if b.is_displayed():
                    connect_btn = b
                    break
            except Exception:
                continue

        if connect_btn:
            driver.execute_script("arguments[0].click();", connect_btn)
            time.sleep(1.5)

            # After clicking Connect, try to open "Add a note" OR detect editor
            for _ in range(8):  # ~4s loop
                # 1) Click "Add a note" if visible
                try:
                    note_buttons = driver.find_elements(
                        By.XPATH,
                        "//button[@aria-label='Add a note' or "
                        "        .//span[normalize-space(text())='Add a note']]",
                    )
                    for nb in note_buttons:
                        if nb.is_displayed():
                            driver.execute_script("arguments[0].click();", nb)
                            time.sleep(1.5)
                            log("🔗 Opened Connect → Add a note (free outreach)")
                            return "note"
                except Exception:
                    pass

                # 2) If the note editor is already open, we’re done
                editors = driver.find_elements(
                    By.XPATH, "//textarea | //div[@role='textbox']"
                )
                if any(getattr(ed, "is_displayed", lambda: False)() for ed in editors):
                    log("🔗 Connect: note editor already open (no 'Add a note' button).")
                    return "note"

                time.sleep(0.5)

            log(
                "⚠ Connect clicked, but no 'Add a note' or editor found "
                "(probably no free note)."
            )
            return "follow_only"

    except Exception as e:
        log(f"ℹ Connect path failed or not present: {e}")

    # ---------- 2. TRY NORMAL MESSAGE (ONLY IF NO CONNECT) ----------
    try:
        msg_btn = None
        msg_candidates = driver.find_elements(
            By.XPATH,
            "//button[@aria-label and contains(@aria-label,'Message')]",
        )
        for b in msg_candidates:
            try:
                if b.is_displayed():
                    msg_btn = b
                    break
            except Exception:
                continue

        if msg_btn:
            driver.execute_script("arguments[0].click();", msg_btn)
            time.sleep(2)
            log("💬 Opened Message window")
            return "message"

    except Exception as e:
        log(f"ℹ Message path failed: {e}")

    # ---------- 3. FOLLOW-ONLY / INMAIL / PAYWALL ----------
    try:
        follow_btn = None
        follow_candidates = driver.find_elements(
            By.XPATH,
            "//button[contains(@aria-label,'Follow') or "
            "        .//span[normalize-space(text())='Follow']]",
        )
        for b in follow_candidates:
            try:
                if b.is_displayed():
                    follow_btn = b
                    break
            except Exception:
                continue

        if follow_btn:
            driver.execute_script("arguments[0].click();", follow_btn)
            time.sleep(1)
            log("👤 Follow-only or InMail profile (no free messaging).")
            return "follow_only"
    except Exception:
        pass

    # ---------- 4. NOTHING USABLE ----------
    log("⚠ No Connect / Message / Follow button found on this profile.")
    return None


def send_message_in_modal(driver, text, expected_name, skip_modal_check=False):
    """
    If skip_modal_check=True, we do NOT verify the modal recipient name.
    Used for 'Add a note' because LinkedIn's note modal rarely includes the name.
    """

    time.sleep(1)

    # SAFETY CHECK — only when NOT skipping modal check
    if not skip_modal_check:
        if not verify_message_modal_recipient(driver, expected_name):
            return False

    # 1) Find the editor box
    editor = None
    for _ in range(8):
        candidates = driver.find_elements(By.XPATH, "//textarea | //div[@role='textbox']")
        for ed in candidates:
            if ed.is_displayed():
                editor = ed
                break
        if editor:
            break
        time.sleep(0.5)

    if not editor:
        log("❌ Could not find editor textarea.")
        return False

    # Type message
    driver.execute_script("arguments[0].focus();", editor)
    editor.send_keys(text)
    time.sleep(1)

    # Find Send button
    send_btn = None
    send_xpaths = [
        "//button[.//span[normalize-space(text())='Send'] and not(@disabled)]",
        "//button[contains(@class,'msg-form__send-button') and not(@disabled)]",
        "//button[@aria-label='Send invitation' and not(@disabled)]"
    ]

    for xp in send_xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed():
                send_btn = btn
                break
        except:
            continue

    if not send_btn:
        log("❌ No send button found.")
        return False

    driver.execute_script("arguments[0].click();", send_btn)
    time.sleep(2)

    log("📨 Message / Note sent successfully.")
    return True


def send_connection_message(driver, row, first_message=True):
    linkedin_url = row.get("linkedin_url", "")
    sheet_name = row.get("recruiter_name", "").strip()
    expected_name = sheet_name if sheet_name else ""

    job_title = row.get("job_title", "Data Analyst")
    company = row.get("company", "")

    if not linkedin_url:
        log("⚠ No LinkedIn URL, skipping.")
        return "UNREACHABLE"

    # Open the profile first
    driver.get(linkedin_url)
    time.sleep(4)

    # --- NEW LOGIC: Extract REAL LinkedIn profile name first ---
    try:
        real_name = driver.find_element(By.XPATH, "//h1").text.strip()
        if real_name:
            log(f"🔄 Extracted real LinkedIn profile name: {real_name}")
            expected_name = real_name   # We now use real name for safety checks
    except Exception:
        pass

    # SAFETY CHECK using REAL profile name
    if expected_name.lower() in ["hiring team", "talent team", "recruiting team"]:
        log("⚠ Generic recruiter name detected—skipping strict profile verification.")
    else:
        if not verify_profile_loaded(driver, expected_name):
            log(f"⚠ SAFETY BLOCKED: Profile mismatch for {expected_name}")
            return False

    # Choose correct message templates
    if first_message:
        full_template = FIRST_MESSAGE_FULL
        short_template = FIRST_MESSAGE_SHORT
    else:
        full_template = FOLLOWUP_MESSAGE_FULL
        short_template = FOLLOWUP_MESSAGE_SHORT

    msg_full = full_template.format(name=expected_name, job_title=job_title, company=company)
    msg_short = short_template.format(name=expected_name, job_title=job_title, company=company)

    # Decide which LinkedIn messaging mode is available
    mode = open_message_or_connect(driver)

    if mode == "note":
        msg_text = shorten_for_note(msg_short, 300)
        log(f"✂ Using NOTE version ({len(msg_text)} chars).")
        ok = send_message_in_modal(driver, msg_text, expected_name, skip_modal_check=True)
        return True if ok else False

    elif mode == "message":
        log("✉ Using FULL message version.")
        ok = send_message_in_modal(driver, msg_full, expected_name)
        return True if ok else False

    elif mode in ("follow_only", None):
        log("ℹ No messaging available. Marking unreachable.")
        return "UNREACHABLE"

def run_first_messages():
    sheet = get_sheet()
    records = get_rows(sheet)
    driver = init_driver()

    count = 0
    today = dt.date.today().isoformat()

    try:
        for i, row in enumerate(records):
            if count >= DAILY_MAX_FIRST:
                log("🚫 Daily first-message limit reached.")
                break

            status = row.get("status", "").lower()
            m1 = str(row.get("message1_sent", "")).upper()

            if status == "pending" and m1 not in ("TRUE", "YES"):
                result = send_connection_message(driver, row, first_message=True)

                if result is True:
                    count += 1
                    update_row(
                        sheet,
                        i,
                        message1_sent="TRUE",
                        last_contacted=today,
                        status="connected",
                    )
                    log(f"✅ Sent first message to {row.get('recruiter_name')}")
                elif result == "UNREACHABLE":
                    # mark as done so we don't keep retrying
                    update_row(
                        sheet,
                        i,
                        message1_sent="TRUE",
                        status="unreachable",
                    )
                    log(
                        f"🚫 Marked unreachable (no free contact): {row.get('recruiter_name')}"
                    )
                else:
                    log(
                        f"⚠ Skipped sending to {row.get('recruiter_name')} due to safety check."
                    )

                random_delay()
    finally:
        driver.quit()


def run_followups(days_wait=3):
    sheet = get_sheet()
    records = get_rows(sheet)
    driver = init_driver()

    count = 0
    today = dt.date.today()

    try:
        for i, row in enumerate(records):
            if count >= DAILY_MAX_FOLLOWUPS:
                log("🚫 Daily follow-up limit reached.")
                break

            status = row.get("status", "").lower()
            m1 = str(row.get("message1_sent", "")).upper()
            m2 = str(row.get("message2_sent", "")).upper()
            last = row.get("last_contacted", "")

            if status in ("pending", "connected") and m1 == "TRUE" and m2 != "TRUE" and last:
                try:
                    last_date = dt.date.fromisoformat(last)
                except ValueError:
                    continue

                if (today - last_date).days >= days_wait:
                    result = send_connection_message(
                        driver, row, first_message=False
                    )
                    if result is True:
                        count += 1
                        update_row(
                            sheet,
                            i,
                            message2_sent="TRUE",
                            last_contacted=today.isoformat(),
                        )
                        log(f"🔁 Sent follow-up to {row.get('recruiter_name')}")
                    elif result == "UNREACHABLE":
                        update_row(sheet, i, message2_sent="TRUE", status="unreachable")
                        log(
                            f"🚫 Follow-up unreachable for: {row.get('recruiter_name')}"
                        )
                    else:
                        log(
                            f"⚠ Skipped follow-up to {row.get('recruiter_name')} due to safety check."
                        )
                    random_delay()
    finally:
        driver.quit()


# ----------------------------------------------------
# Streamlit UI
# ----------------------------------------------------
def main():
    st.title("💼 Job Outreach Automation – Akhil Bot")

    st.markdown(
        """
Use the buttons below to run each step:

1. **Collect applied jobs** from JobRight  
2. **Extract recruiters** from those jobs into Google Sheets  
3. **Send first messages** to recruiters  
4. **Send follow-ups** after a few days  
"""
    )

    mode = st.radio(
        "Choose action:",
        [
            "[1] Collect applied jobs from JobRight",
            "[2] Extract recruiters from applied jobs",
            "[3] Send first recruiter messages",
            "[4] Send follow-up messages",
        ],
    )

    if mode.startswith("[1]"):
        if st.button("Run: Collect applied jobs"):
            collect_applied_jobright_urls()

    elif mode.startswith("[2]"):
        if st.button("Run: Extract recruiters into Google Sheet"):
            run_extract_recruiters()

    elif mode.startswith("[3]"):
        st.write(f"Daily max first messages: {DAILY_MAX_FIRST}")
        if st.button("Run: Send first messages"):
            run_first_messages()

    elif mode.startswith("[4]"):
        days_wait = st.number_input(
            "Days to wait before follow-up:", min_value=1, max_value=30, value=3
        )
        if st.button("Run: Send follow-ups"):
            run_followups(days_wait=int(days_wait))


if __name__ == "__main__":
    main()