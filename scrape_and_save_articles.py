import feedparser
import logging
import os
import re
from datetime import datetime
from dateutil import parser as date_parser
from datetime import datetime
import logging
from supabase import create_client, Client
from google.cloud import pubsub_v1
import json
from openai import OpenAI

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")  # Default if not set
url = os.getenv("AUDIO_GENERATION_FUNCTION_URL")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI()

# List of RSS Feeds
RSS_FEEDS = [
    'https://www.portfolio.hu/rss/all.xml',
    'https://index.hu/24ora/rss/'
]

def remove_html_tags(input_string):
    """
    Remove HTML tags from the given string.
    
    Args:
        input_string (str): The string containing HTML tags.
        
    Returns:
        str: The string with HTML tags removed.
    """
    # Regular expression to match HTML tags
    clean_text = re.sub(r'<[^>]+>', '', input_string)
    return clean_text

def clean_text(text: str) -> str:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an HTML cleaner."},
            {
                "role": "user",
                "content": f"Clean the following data by removing unnecessary characters, typos and by converting numbers to words: '{text}'. Return only the raw processed text."
            }
        ]
    )
    return completion.choices[0].message.content

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
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path("currentlyai", "articles-saved")

    for url in RSS_FEEDS:
        logging.info(f"Scraping RSS feed: {url}")
        feed = feedparser.parse(url)

        for entry in feed.entries[:1]:
            # Check if article exists
            response = supabase.table("article").select("*").eq("link", entry.link).execute()
            if response.data:
                logging.info(f"Article already exists in database: {entry.title}")
                continue

            # Process and save new article
            pub_date = parse_pub_date(entry)
            text_without_tags = remove_html_tags(entry.description)
            processed_text = clean_text(text_without_tags)
            article_data = {
                "title": entry.title,
                "description": processed_text,
                "pub_date": pub_date.isoformat(),
                "link": entry.link,
                "category": entry.get("category", "Uncategorized")
            }
            insert_response = supabase.table("article").insert(article_data).execute()
            article_id = insert_response.data[0]['id']

            logging.info(f"New article saved with ID {article_id}: {entry.title}")

            # Publish to Pub/Sub for audio generation
            article_message = {
                "article_id": str(article_id),           # Convert article_id to string
                "title": str(entry.title),               # Ensure title is a string
                "description": str(processed_text),      # Ensure description is a string
                "pub_date": pub_date.isoformat(),        # Ensure pub_date is an ISO-formatted string
                "link": str(entry.link)                  # Ensure link is a string
            }
            future = publisher.publish(topic_path, json.dumps(article_message).encode("utf-8"))
            print(f"Message id: {future.result()}")

    return "RSS scraping complete", 200
