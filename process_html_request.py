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
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")  # Default bucket name

# Initialize Supabase and OpenAI clients
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
}

def number_to_hungarian(num: int) -> str:
    """Convert a number to its Hungarian name."""
    if num in NUMBER_WORDS_HU:
        return NUMBER_WORDS_HU[num]
    elif num < 100:
        tens, remainder = divmod(num, 10)
        return f"{NUMBER_WORDS_HU[tens * 10]}{NUMBER_WORDS_HU[remainder]}"
    elif num == 100:
        return NUMBER_WORDS_HU[100]
    else:
        return str(num)  # Fallback for numbers not in the dictionary

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
    """Process HTML content and publish cleaned data."""
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GOOGLE_CLOUD_PROJECT, "html-processed")

    # Extract and clean HTML content
    main_content = extract_main_content(html)
    cleaned_text = clean_text(main_content)

    # Prepare data for Supabase and Pub/Sub
    article_data = {
        "page_id": page_id,
        "cleaned_content": cleaned_text,
        "processed_at": datetime.now().isoformat()
    }

    # Save cleaned content to Supabase
    response = supabase.table("article").insert(article_data).execute()
    if response.error:
        logging.error(f"Failed to save article to Supabase: {response.error}")
        raise Exception(response.error)

    logging.info(f"Cleaned HTML content saved with page ID: {page_id}")

    # Publish cleaned content to Pub/Sub
    message = {
        "page_id": page_id,
        "cleaned_content": cleaned_text
    }
    future = publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
    logging.info(f"Message published to Pub/Sub with ID: {future.result()}")

    return article_data

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
