
import asyncio
import json
import logging
import os
import random
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_TOKEN = ''
client = WebClient(token='')


class SearchOption:
    def __init__(self, url, channel_id):
        self.url = url
        self.channel_id = channel_id
# Configuration
SEARCH_CONFIG = [
    SearchOption(
        'https://www.upwork.com/nx/search/jobs/?category2_uid=531770282580668418&hourly_rate=25-&is_sts_vector_search_result=false&location=Caribbean,Central%20America,Australia,Austria,Bahamas,Belgium,Brazil,Canada,Cyprus,Denmark,Estonia,Finland,Germany,Greenland,Iceland,Israel,Japan,Liechtenstein,Lithuania,Luxembourg,Maldives,Malta,Netherlands,New%20Zealand,Norway,Panama,Philippines,Poland,Portugal,Qatar,Samoa,Saudi%20Arabia,Singapore,South%20Korea,Sweden,Switzerland,United%20Arab%20Emirates,United%20Kingdom,United%20States&nav_dir=pop&payment_verified=1&per_page=50&q=react&sort=recency&t=0',
        '#react-feed'
    ),
    SearchOption(
        'https://www.upwork.com/nx/search/jobs/?category2_uid=531770282580668418&hourly_rate=25-&is_sts_vector_search_result=false&location=Caribbean,Central%20America,Australia,Austria,Bahamas,Belgium,Brazil,Canada,Cyprus,Denmark,Estonia,Finland,Germany,Greenland,Iceland,Israel,Japan,Liechtenstein,Lithuania,Luxembourg,Maldives,Malta,Netherlands,New%20Zealand,Norway,Panama,Philippines,Poland,Portugal,Qatar,Samoa,Saudi%20Arabia,Singapore,South%20Korea,Sweden,Switzerland,United%20Arab%20Emirates,United%20Kingdom,United%20States&nav_dir=pop&payment_verified=1&per_page=50&q=saas&sort=recency&t=0',
        '#saas-feed'
    ),
]

CSV_FILE = 'upwork_jobs.csv'
HTML_FILE = 'upwork_page.html'
SCREENSHOT_FILE = 'upwork_screenshot.png'
COOKIES_FILE = 'cookies.json'
SERVICE_ACCOUNT_FILE = 'vivid-carrier-439021-v6-83c595ea05dc.json'

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('upwork_parser.log', mode='w'), logging.StreamHandler()]
)

# Hardcoded User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36"
)

# Filters
KEYWORDS = ['']

def load_existing_jobs_from_csv():
    if os.path.exists(CSV_FILE) and os.stat(CSV_FILE).st_size > 0:
        df = pd.read_csv(CSV_FILE, on_bad_lines='skip')
        return set(df['Link'].tolist())
    return set()

async def fetch_jobs(config, existing_jobs): #теперь принимает  URl из списка
    async with async_playwright() as p:
        logging.info(f"Launching browser for URL: {config.url}...")  #Добавил для наглядности
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=USER_AGENT
        )

        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()
        logging.info("Navigating to Upwork...")
        await page.goto(config.url)

        for _ in range(5):
            await page.mouse.wheel(0, 1000)
            await page.wait_for_timeout(2000)

        html_content = await page.content()
        with open(HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logging.info(f"HTML saved to: {HTML_FILE}")

        try:
            job_elements = await page.query_selector_all('[data-test="JobTile"]')
            logging.info(f"Found {len(job_elements)} job elements.")
        except TimeoutError:
            logging.error("Timeout while waiting for job elements.")
            return []

        jobs = []
        for i, job_element in enumerate(job_elements):
            try:
                title_element = await job_element.query_selector('.up-n-link')
                title = await title_element.inner_text() if title_element else "No title"
                link = 'https://www.upwork.com' + (await title_element.get_attribute('href') if title_element else "")

                if link in existing_jobs:
                    logging.info(f"Job '{title}' already exists. Skipping.")
                    continue

                description_element = await job_element.query_selector('div[data-test="UpCLineClamp JobDescription"]')
                description = await description_element.inner_text() if description_element else "No description"

                budget_element = await job_element.query_selector('li[data-test="is-fixed-price"] strong:last-child')
                budget_text = await budget_element.inner_text() if budget_element else "0"
                budget = f"Fixed: ${float(budget_text.replace(',', '').replace('$', '').strip())}" if budget_text else 0.0
                if budget == "Fixed: $0.0":
                    budget_element = await job_element.query_selector('ul[data-test="JobInfo"] li[data-test="job-type-label"] strong:last-child')
                    budget_text = await budget_element.inner_text() if budget_element else "0"
                    budget = budget_text if budget_text else 0.0

                published_date = datetime.now()

                job_tags_elements = await job_element.query_selector_all(
                    'div[data-test="TokenClamp JobAttrs"] .air3-token span')
                job_tags = [await tag.inner_text() for tag in job_tags_elements]

                if not any(keyword.lower() in title.lower() for keyword in KEYWORDS):
                    logging.info(f"Job '{title}' did not pass filters. Skipping.")
                    continue

                job_data = {
                    'Title': title,
                    'Description': description,
                    'Link': link,
                    'Budget/Rate': budget,
                    'job_tags': job_tags,
                    'date': published_date
                }
                send_to_slack(config.channel_id, title, description, budget, job_tags, link)
                jobs.append(job_data)
                logging.info(f"Found job: {title} with budget {budget}, published on {published_date}")

            except Exception as e:
                logging.error(f"Error processing job {i}: {e}", exc_info=True)

        await browser.close()
        return jobs

def save_to_csv(jobs):
    try:
        df = pd.DataFrame(jobs)
        if not os.path.exists(CSV_FILE) or os.stat(CSV_FILE).st_size == 0:
            df.to_csv(CSV_FILE, index=False)
        else:
            existing_df = pd.read_csv(CSV_FILE, on_bad_lines='skip')
            combined_df = pd.concat([existing_df, df]).drop_duplicates(subset='Link', keep='last')
            combined_df.to_csv(CSV_FILE, index=False)
        logging.info(f"Saved {len(jobs)} jobs to {CSV_FILE}.")
    except Exception as e:
        logging.error(f"Error saving to CSV: {e}", exc_info=True)

def load_cookies():
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, 'r') as f:
            return json.load(f)
    return None

def send_to_slack(channel_id, title, description, budget, job_tags, link):
    emojis = [":skull:", ":star:", ":rocket:", ":fire:", ":zap:", ":bulb:", ":moneybag:", ":dart:", ":sparkles:"]
    chosen_emoji = random.choice(emojis)  # Случайный выбор эмодзи

    try:
        response = client.chat_postMessage(
            channel=channel_id,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{chosen_emoji} *{title}*\n\n_{description}_\n\n*Budget:* `{budget}`\n*Tags:* {', '.join(job_tags)}"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Job Offer :arrow_right:"
                            },
                            "url": link,
                            "style": "primary"
                        }
                    ]
                }
            ]
        )
        print("Job offer message sent:", response["message"]["text"])
    except SlackApiError as e:
        print("Error sending job offer message: {}".format(e.response["error"]))

async def main():
    logging.info("Starting job parsing...")
    existing_jobs = load_existing_jobs_from_csv()
    all_jobs = []

    for config in SEARCH_CONFIG:
        jobs = await fetch_jobs(config, existing_jobs)
        all_jobs.extend(jobs)

    if all_jobs:
        save_to_csv(all_jobs)
    else:
        logging.info("No new jobs found.")

if __name__ == '__main__':
    asyncio.run(main())