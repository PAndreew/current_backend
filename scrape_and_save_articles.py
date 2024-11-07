import feedparser
import logging
import os
from datetime import datetime
from dateutil import parser as date_parser
from datetime import datetime
import logging
from supabase import create_client, Client
from google.cloud import tasks_v2
import json

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")  # Default if not set
url = os.getenv("AUDIO_GENERATION_FUNCTION_URL")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# List of RSS Feeds
RSS_FEEDS = [
    'https://www.portfolio.hu/rss/all.xml',
    'https://index.hu/24ora/rss/'
]

def parse_pub_date(entry) -> datetime:
    """Parse the published date from an RSS entry."""
    if 'published_parsed' in entry:
        return datetime(*entry.published_parsed[:6])
    elif 'published' in entry:
        try:
            return date_parser.parse(entry.published)
        except (ValueError, TypeError) as e:
            logging.warning(f"Failed to parse date '{entry.published}': {e}")
    # Default to the current time if parsing fails or date is missing
    return datetime.now()

def scrape_and_save_articles(request):
    for url in RSS_FEEDS:
        logging.info(f"Scraping RSS feed: {url}")
        feed = feedparser.parse(url)
        new_article_found = False

        for entry in feed.entries[:1]:
            response = supabase.table("article").select("*").eq("link", entry.link).execute()
            existing_article = response.data

            if not existing_article:
                pub_date = parse_pub_date(entry)
                article_data = {
                    "title": entry.title,
                    "description": entry.description,
                    "pub_date": pub_date.isoformat(),
                    "link": entry.link,
                    "category": entry.get("category", "Uncategorized")
                }
                
                # Insert article and get the response with ID
                insert_response = supabase.table("article").insert(article_data).execute()
                new_article_id = insert_response.data[0]['id']  # Extract the ID from the response
                
                logging.info(f"New article saved with ID {new_article_id}: {entry.title}")

                # Schedule audio generation task with the ID
                schedule_audio_task(new_article_id)
                new_article_found = True
                break
            else:
                logging.info(f"Article already exists in database: {entry.title}")

        if not new_article_found:
            logging.info("No new articles found in this feed.")

    return "RSS scraping complete", 200

def schedule_audio_task(article_id):
    client = tasks_v2.CloudTasksClient()
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    queue = "audio-generation-queue"
    location = "us-central1"
    url = "https://us-central1-currentlyai.cloudfunctions.net/generate_audio_for_article"
    payload = {"article_id": article_id}  # Send the integer ID instead of the link

    parent = client.queue_path(project, location, queue)

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
        }
    }

    client.create_task(parent=parent, task=task)
