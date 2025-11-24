import logging
import os
import re
from datetime import datetime, timedelta
import json

from supabase import create_client, Client
from google.cloud import pubsub_v1
from perplexity import Perplexity
# from dotenv import load_dotenv
from num2words import num2words
from dateutil import parser as date_parser

# Load environment variables from .env file
# load_dotenv()

# --- Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Perplexity client
client = Perplexity(api_key=PERPLEXITY_API_KEY)

# --- News Categories ---
NEWS_CATEGORIES = [
    "finance", "sports", "technology", "politics", "world news",
    "entertainment", "health", "science", "business", "lifestyle"
]

def convert_numbers_to_words_hu(text):
    """
    Finds numbers in a Hungarian text and converts them to words.
    """
    if not text:
        return text
    number_pattern = re.compile(r'\b\d+,\d+\b|\b\d+\b')

    def replace_with_words(match):
        number_str = match.group(0)
        try:
            number_val = float(number_str.replace(',', '.')) if ',' in number_str else int(number_str)
            return num2words(number_val, lang='hu')
        except (ValueError, TypeError):
            return number_str
    return number_pattern.sub(replace_with_words, text)

def get_perplexity_completion(prompt, search_after_date_filter=None):
    """
    Gets the full completion response from the Perplexity API.
    """
    try:
        messages = [{"role": "user", "content": prompt}]
        completion = client.chat.completions.create(
            model="sonar",
            messages=messages,
            search_after_date_filter=search_after_date_filter
        )
        return completion
    except Exception as e:
        logging.error(f"Error calling Perplexity API: {e}")
        return None

def parse_article_date(date_string):
    """Safely parses a date string and returns an ISO format string."""
    try:
        dt = date_parser.parse(date_string)
        return dt.isoformat()
    except (ValueError, TypeError):
        logging.warning(f"Could not parse date '{date_string}'. Defaulting to now.")
        return datetime.now().isoformat()

def scrape_and_save_articles(request):
    """
    Cloud Function entry point that generates summaries and publishes a
    message with the correct schema to Pub/Sub.
    """
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GOOGLE_CLOUD_PROJECT, "articles-saved")
    two_hours_ago = datetime.now() - timedelta(hours=2)
    date_filter = two_hours_ago.strftime("%m/%d/%Y")
    processed_urls = set()

    for category in NEWS_CATEGORIES:
        logging.info(f"--- Generating summary for category: {category.upper()} ---")
        category_prompt = f"Summarize the most important Hungarian news in the '{category}' category from the last 2 hours. The summary should be in Hungarian and about 100 words long. Include multiple sources."
        
        category_completion = get_perplexity_completion(category_prompt, search_after_date_filter=date_filter)

        if not category_completion or not category_completion.search_results:
            logging.warning(f"Could not get a summary or search results for '{category}'. Skipping.")
            continue

        logging.info(f"Category '{category}' summary generated with {len(category_completion.search_results)} sources.")

        for article_info in category_completion.search_results:
            url = article_info.url
            if not url or url in processed_urls:
                continue
            
            response = supabase.table("article").select("id").eq("link", url).execute()
            if response.data:
                logging.info(f"Article from this URL already exists: {url}")
                processed_urls.add(url)
                continue

            logging.info(f"Generating detailed summary for article: {url}")
            
            article_prompt = f"Summarize the article from this URL in Hungarian, using about 70-100 words: {url}. Please provide a suitable title for the summary based on the article's content."
            article_completion = get_perplexity_completion(article_prompt)

            if not article_completion or not article_completion.choices:
                logging.warning(f"Failed to get a detailed summary for article: {url}")
                continue
            
            snippet_text = article_info.snippet if article_info.snippet else ''
            description_with_words = convert_numbers_to_words_hu(snippet_text)

            detailed_summary = article_completion.choices[0].message.content
            full_text_with_words = convert_numbers_to_words_hu(detailed_summary)
            
            title_match = re.match(r"^\s*#*\s*([^#\n\r]+)", detailed_summary)
            final_title = title_match.group(1).strip() if title_match else article_info.title
            
            publication_date_iso = parse_article_date(article_info.date)

            db_record = {
                "title": final_title,
                "description": description_with_words,
                "full_text": full_text_with_words,
                "pub_date": publication_date_iso,
                "link": url,
                "category": category
            }

            insert_response = supabase.table("article").insert(db_record).execute()
            if not insert_response.data:
                logging.error(f"Failed to insert article into Supabase for URL: {url}")
                continue
            
            article_id = insert_response.data[0]['id']
            logging.info(f"New article saved with ID {article_id}: {final_title}")

            # --- CORRECTED PUBSUB MESSAGE ---
            # This dictionary now includes the 'description' field and matches the required schema.
            pubsub_message = {
                "article_id": str(article_id),
                "title": str(final_title),
                "description": str(description_with_words),
                "full_text": str(full_text_with_words),
                "pub_date": publication_date_iso,
                "link": str(url)
            }
            # --- END CORRECTION ---
            
            future = publisher.publish(topic_path, json.dumps(pubsub_message).encode("utf-8"))
            try:
                message_id = future.result()
                logging.info(f"Message {message_id} published for article ID {article_id}.")
            except Exception as e:
                logging.error(f"Failed to publish message for article ID {article_id}: {e}")

            processed_urls.add(url)

    return "News summary generation and saving complete.", 200

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    scrape_and_save_articles(None)