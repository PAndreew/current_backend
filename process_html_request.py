import os
import re
import logging
from datetime import datetime
from supabase import create_client, Client
from google.cloud import pubsub_v1
import json
from openai import OpenAI
from bs4 import BeautifulSoup  # For HTML parsing

# Environment Variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
# # OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")  # Default bucket name

# # Initialize Supabase and OpenAI clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# client = OpenAI()

NUMBER_WORDS_HU = {
    0: "nulla",
    1: "egy",
    2: "kettő",
    3: "három",
    4: "négy",
    5: "öt",
    6: "hat",
    7: "hét",
    8: "nyolc",
    9: "kilenc",
    10: "tíz",
    11: "tizenegy",
    12: "tizenkettő",
    13: "tizenhárom",
    14: "tizennégy",
    15: "tizenöt",
    16: "tizenhat",
    17: "tizenhét",
    18: "tizennyolc",
    19: "tizenkilenc",
    20: "húsz",
    30: "harminc",
    40: "negyven",
    50: "ötven",
    60: "hatvan",
    70: "hetven",
    80: "nyolcvan",
    90: "kilencven",
    100: "száz",
    1000: "ezer"
}

LARGE_UNITS_HU = [
    (10**12, "billió"),
    (10**9, "milliárd"),
    (10**6, "millió"),
    (10**3, "ezer"),
]

def number_to_hungarian(num: int) -> str:
    """Convert a number to its Hungarian name."""
    if num == 0:
        return NUMBER_WORDS_HU[0]

    if num < 20:
        return NUMBER_WORDS_HU[num]

    if num < 100:
        tens, remainder = divmod(num, 10)
        return f"{NUMBER_WORDS_HU[tens * 10]}{NUMBER_WORDS_HU[remainder]}" if remainder else NUMBER_WORDS_HU[tens * 10]

    if num < 1000:
        hundreds, remainder = divmod(num, 100)
        prefix = f"{NUMBER_WORDS_HU[hundreds]}{NUMBER_WORDS_HU[100]}" if hundreds > 1 else NUMBER_WORDS_HU[100]
        return f"{prefix}{number_to_hungarian(remainder)}".strip() if remainder else prefix

    for value, name in LARGE_UNITS_HU:
        if num >= value:
            major, remainder = divmod(num, value)
            major_part = number_to_hungarian(major)
            remainder_part = f" {number_to_hungarian(remainder)}" if remainder else ""
            return f"{major_part}{name}{remainder_part}".strip()

    # Fallback for unexpected cases
    return str(num)

def replace_numbers_with_words(text: str) -> str:
    """Replace numbers in the text with their Hungarian word equivalents."""
    def replace_match(match):
        num = int(match.group())
        return number_to_hungarian(num)

    # Regex to find numbers in the text
    return re.sub(r'\b\d+\b', replace_match, text)

def clean_text(text: str) -> str:
    """
    Clean text by converting numbers to their Hungarian word equivalents.
    """
    cleaned_text = replace_numbers_with_words(text)
    return cleaned_text

def extract_main_content(html: str) -> str:
    """Extract main content from HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, 'html.parser')

    # Remove scripts, styles, and unnecessary tags
    for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
        tag.decompose()

    # Extract visible text
    text = soup.get_text(separator=' ', strip=True)
    return text

def process_html_and_publish(page_id: str, html: str):
    """Process HTML content, save to Supabase, and publish to Pub/Sub."""
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GOOGLE_CLOUD_PROJECT, "articles-saved")  # Use the existing topic

    try:
        # Step 1: Extract and clean HTML content
        logging.info("Extracting and cleaning HTML content.")
        main_content = extract_main_content(html)
        cleaned_text = clean_text(main_content)

        # Step 2: Prepare article data
        logging.info("Preparing article data.")
        article_data = {
            "id": page_id,  # Use page_id as the article ID
            "title": f"Test Title for {page_id}",  # Placeholder for title
            "description": cleaned_text[:100],  # Cleaned content as description
            "pub_date": datetime.now().isoformat(),  # Placeholder publication date
            "link": f"https://example.com/articles/{page_id}",  # Placeholder link
            "category": "Uncategorized"  # Default category
        }

        # Step 3: Insert article into Supabase
        logging.info(f"Inserting article with ID {page_id} into Supabase.")
        response = supabase.table("article").insert(article_data).execute()

        # Check for errors in Supabase response
        if "error" in response and response["error"]:
            raise Exception(f"Supabase error: {response['error']}")

        logging.info(f"Article successfully saved with ID {page_id}.")

        # Step 4: Publish cleaned content to Pub/Sub
        logging.info(f"Publishing message for article ID {page_id} to Pub/Sub.")
        message = {
            "article_id": str(page_id),
            "title": article_data["title"],
            "description": article_data["description"],
            "pub_date": article_data["pub_date"],
            "link": article_data["link"]
        }

        # Publish the message
        future = publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
        pubsub_message_id = future.result()
        logging.info(f"Message published to Pub/Sub with ID: {pubsub_message_id}")

        return article_data

    except Exception as e:
        logging.error(f"Error in process_html_and_publish: {e}")
        raise

def process_html_request(request):
    """Entry point for processing HTML requests."""
    if request.method != 'POST':
        return "Method not allowed", 405

    try:
        data = request.get_json()
        page_id = data.get('page_id')
        html_content = data.get('html')

        if not page_id or not html_content:
            raise ValueError("Missing required fields: 'page_id' and 'html'.")

        result = process_html_and_publish(page_id, html_content)
        return {"status": "success", "data": result}, 200
    except Exception as e:
        logging.error(f"Error processing request: {e}")
        return {"status": "error", "message": str(e)}, 500
