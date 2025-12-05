import os
import time
import datetime as dt
import random
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException
)

# Load environment variables
load_dotenv()

GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")
DAILY_MAX_FIRST = int(os.getenv("DAILY_MAX_FIRST_MESSAGES", "20"))
DAILY_MAX_FOLLOWUPS = int(os.getenv("DAILY_MAX_FOLLOWUPS", "10"))

# -------------------------------------------------------------------
# Message templates
# -------------------------------------------------------------------
FIRST_MESSAGE_TEMPLATE = """Hi {name},

I came across your profile while exploring opportunities for a {job_title} role at {company}.
I’ve been working on SQL, Python, ETL, and dashboards in banking and healthcare (TD Bank, Availity).

If you're hiring for a Data Analyst / Reporting Analyst role, I’d really appreciate a quick review of my background.

Thanks,
Akhil
"""

FOLLOWUP_MESSAGE_TEMPLATE = """Hi {name},

Just following up on my previous note regarding opportunities at {company}.
If there's someone else on your team who handles analytics hiring, I’d be grateful if you could point me to them.

Thanks again,
Akhil
"""


# -------------------------------------------------------------------
# Google Sheets setup
# -------------------------------------------------------------------
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "service_account.json", scope
    )
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1


def get_rows(sheet):
    return sheet.get_all_records()


def update_row(sheet, index, **kwargs):
    row_number = index + 2  # sheet rows start from row 2
    header = sheet.row_values(1)

    for col_name, value in kwargs.items():
        if col_name in header:
            col_index = header.index(col_name) + 1
            sheet.update_cell(row_number, col_index, value)


# -------------------------------------------------------------------
# Initialize Chrome Driver
# -------------------------------------------------------------------
def init_driver():
    options = webdriver.ChromeOptions()

    # Use local Chrome profile to stay logged in
    options.add_argument("user-data-dir=" + os.path.expanduser("~") + "/.jobbot-profile")
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    # Selenium 4 requires Service object
    service = Service(CHROME_DRIVER_PATH)

    driver = webdriver.Chrome(service=service, options=options)

    driver.get("https://www.linkedin.com")
    print("👉 Log into LinkedIn if needed.")
    input("Press ENTER once logged in and on the homepage...")

    return driver


# -------------------------------------------------------------------
# Send LinkedIn message
def send_connection_message(driver, row, first_message=True):
    linkedin_url = row.get("linkedin_url", "")
    name = row.get("recruiter_name", "there")
    job_title = row.get("job_title", "Data Analyst")
    company = row.get("company", "")

    message_text = (FIRST_MESSAGE_TEMPLATE if first_message else FOLLOWUP_MESSAGE_TEMPLATE).format(
        name=name,
        job_title=job_title,
        company=company
    )

    if not linkedin_url:
        print("⚠ No LinkedIn URL, skipping.")
        return False

    driver.get(linkedin_url)
    time.sleep(5)

    # ----------------------------------------------------
    # TRY CONNECT FLOW
    # ----------------------------------------------------
    try:
        connect_btn = driver.find_element(By.XPATH, "//button[contains(., 'Connect')]")
        driver.execute_script("arguments[0].click();", connect_btn)
        time.sleep(2)

        # Add a note button
        try:
            note_btn = driver.find_element(By.XPATH, "//button[contains(., 'Add a note')]")
            driver.execute_script("arguments[0].click();", note_btn)
            time.sleep(2)
        except:
            pass

        # Text area inside connect modal
        textbox_candidates = driver.find_elements(By.XPATH, "//div[@role='textbox']")
        textbox = next((t for t in textbox_candidates if t.is_displayed()), None)

        if textbox is None:
            raise Exception("❌ No visible textbox in connect modal.")

        textbox.click()
        textbox.send_keys(message_text)
        time.sleep(1)

        send_button_xpaths = [
            "//button[contains(@class,'msg-form__send-button')]",  # your exact button
            "//button[contains(., 'Send') and @type='submit']",
            "//button[@aria-label='Send']",
            "//button[contains(@class,'artdeco-button') and contains(., 'Send')]",
            "//button//*[name()='svg']",
        ]

        send_btn = None
        for xp in send_button_xpaths:
            try:
                btn = driver.find_element(By.XPATH, xp)
                if btn.is_displayed():
                    send_btn = btn
                    break
            except:
                continue

        if not send_btn:
            raise Exception("❌ Could not find ANY send button selector.")

        driver.execute_script("arguments[0].click();", send_btn)
        time.sleep(1)
        return True

    except Exception as e:
        print(f"⚠ Connect flow failed: {e}")

    # ----------------------------------------------------
    # TRY MESSAGE FLOW (ALREADY CONNECTED)
    # ----------------------------------------------------
    try:
        # Open message popup
        message_btn = driver.find_element(By.XPATH, "//button[contains(., 'Message')]")
        driver.execute_script("arguments[0].click();", message_btn)
        time.sleep(2)

        dialog = driver.find_element(By.XPATH, "//div[contains(@role,'dialog')]")

        # New LinkedIn editor div
        editor = dialog.find_element(
            By.XPATH,
            ".//div[contains(@class,'msg-form__contenteditable')]"
        )

        editor.click()
        editor.send_keys(message_text)
        time.sleep(1)

        # BLUE SEND BUTTON → THIS IS WHAT LINKEDIN CHANGED
        send_btn = dialog.find_element(
            By.XPATH,
            ".//button[contains(@class,'msg-form__send-button')]"
        )
        driver.execute_script("arguments[0].click();", send_btn)

        return True

    except Exception as e:
        print(f"⚠ Message flow failed: {e}")

    print(f"⚠ No Connect or Message option available on: {linkedin_url}")
    return False


def random_delay():
    time.sleep(random.uniform(5, 10))


# -------------------------------------------------------------------
# First messages mode
# -------------------------------------------------------------------
def run_first_messages():
    sheet = get_sheet()
    records = get_rows(sheet)
    driver = init_driver()

    count = 0
    today = dt.date.today().isoformat()

    for i, row in enumerate(records):
        if count >= DAILY_MAX_FIRST:
            print("🚫 Daily first-message limit reached.")
            break

        status = row.get("status", "").lower()
        m1 = str(row.get("message1_sent", "")).upper()

        if status == "pending" and m1 not in ("TRUE", "YES"):
            success = send_connection_message(driver, row, first_message=True)
            if success:
                count += 1
                update_row(sheet, i,
                           message1_sent="TRUE",
                           last_contacted=today)
                print(f"✅ Sent first message to {row.get('recruiter_name')}")
            random_delay()

    driver.quit()


# -------------------------------------------------------------------
# Follow-up mode
# -------------------------------------------------------------------
def run_followups(days_wait=3):
    sheet = get_sheet()
    records = get_rows(sheet)
    driver = init_driver()

    count = 0
    today = dt.date.today()

    for i, row in enumerate(records):
        if count >= DAILY_MAX_FOLLOWUPS:
            print("🚫 Daily follow-up limit reached.")
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
                success = send_connection_message(driver, row, first_message=False)
                if success:
                    count += 1
                    update_row(sheet, i,
                               message2_sent="TRUE",
                               last_contacted=today.isoformat())
                    print(f"🔁 Sent follow-up to {row.get('recruiter_name')}")
                random_delay()

    driver.quit()


# -------------------------------------------------------------------
# Main entry
# -------------------------------------------------------------------
if __name__ == "__main__":
    print("Choose mode:")
    print("[1] Send first recruiter messages")
    print("[2] Send follow-up messages")
    mode = input("Enter option (1/2): ").strip()

    if mode == "1":
        run_first_messages()
    else:
        run_followups()