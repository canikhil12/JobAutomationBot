🤖 JobAutomationBot — Automated Job Application & Recruiter Outreach Tool

JobAutomationBot is a Python-based automation system that helps streamline your job search.
It automatically:
	•	Applies to saved jobs
	•	Extracts recruiter information
	•	Sends personalized outreach messages
	•	Tracks applications in Google Sheets
	•	Automates LinkedIn coming up / JobRight workflows
	•	Uses Apollo API to fetch recruiter details when LinkedIn doesn’t show them - coming up

This project eliminates repetitive job-search tasks and boosts your outreach efficiency.

⸻

🚀 Features
	•	🔄 Automate recruiter outreach for applied jobs
	•	🕵️ Extract recruiter/HR details using Apollo API
	•	💬 Send personalized messages using templates
	•	📑 Update job status in Google Sheets
	•	🕸️ Scrape applied jobs from LinkedIn
	•	🔐 Secure secret key handling via .env
	•	📈 Boost your job hunt productivity


JobAutomationBot/
│── job_automation_app.py                   # Main automation script
│── .env                     # API keys and secrets
│── README.md                # Documentation
│── requirements.txt         # Python dependencies

🔧 Setup Instructions
1. Clone repo

git clone https://github.com/canikhil12/JobAutomationBot.git
cd JobAutomationBot

2. Create virtual environment

python3 -m venv .venv
source .venv/bin/activate

3. Install dependencies

pip install -r requirements.txt

4. Create .env file

APOLLO_API_KEY=your_key_here
GOOGLE_SHEETS_CREDS_PATH=credentials.json
OPENAI_API_KEY=your_key_here
LINKEDIN_EMAIL=your_email
LINKEDIN_PASSWORD=your_password

▶️ Run the Bot
streamlit run job_automation_app.py

📦 Output
	•	Personalized recruiter messages
	•	Updated Google Sheets tracker
	•	Local logs of outreach
	•	JSON export of job applications

🤝 Contributing
Feel free to open issues or PRs.
