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

# Gemini Pro API setup
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)
generation_config = genai.GenerationConfig(
    temperature=0.2,
    top_p=1,
    top_k=30,
    max_output_tokens=2048,
)
safety_settings = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
]
model = genai.GenerativeModel(model_name="gemini-2.0-flash",
                              generation_config=generation_config,
                              safety_settings=safety_settings)

# List of RSS Feeds
RSS_FEEDS = [
    "https://konteo.blogrepublik.eu/feed/"
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

def scrape_full_article(article_url):
    """Scrapes the full article text from a given URL, looking for 'posztkenyerszoveg' class."""
    try:
        response = requests.get(article_url, timeout=10) # Added timeout
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        soup = BeautifulSoup(response.content, 'html.parser')
        article_div = soup.find('div', class_='posztkenyerszoveg')
        if article_div:
            article_text = article_div.get_text(separator='\n', strip=True) # Using separator and strip for cleaner text
            return article_text
        else:
            logging.warning(f"Div with class 'posztkenyerszoveg' not found on: {article_url}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error scraping URL {article_url}: {e}")
        return None

def translate_text_with_gemini(text, target_language='english'):
    """Translates text to English using Gemini Pro."""
    if not text:
        return None  # Or empty string, depending on how you want to handle empty input

    try:
        prompt = f"Translate the following text to {target_language}: {text}" # More explicit prompt
        response = model.generate_content([prompt])
        if response.text:
            return response.text
        elif response.candidates and response.candidates[0].content.parts: # Handling cases with candidates
            return "".join([part.text for part in response.candidates[0].content.parts if hasattr(part, 'text')])
        else:
            logging.warning(f"Gemini Pro translation failed, empty response for text: {text[:100]}...") # Log first 100 chars
            return None
    except Exception as e: # Catch broader exceptions for Gemini errors
        logging.error(f"Gemini Pro translation error: {e}")
        return None

def scrape_and_save_articles(request):
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path("currentlyai", "articles-saved")

    for url in RSS_FEEDS:
        logging.info(f"Scraping RSS feed: {url}")
        feed = feedparser.parse(url)

        for entry in feed.entries[:1]: # Limiting to first article for testing, remove [:1] for all
            # Check if article exists
            response = supabase.table("article").select("*").eq("link", entry.link).execute()
            if response.data:
                logging.info(f"Article already exists in database: {entry.title}")
                continue

            # Scrape full article text
            full_article_text = scrape_full_article(entry.link)

            # Translate full article text
            translated_text = None
            if full_article_text:
                translated_text = translate_text_with_gemini(full_article_text)
                if translated_text:
                    logging.info(f"Article translated successfully: {entry.title}")
                else:
                    logging.warning(f"Translation failed for article: {entry.title}, using original description.")
                    translated_text = "Translation failed. Original Hungarian text may be available in 'full_text_original' field." # Fallback message
            else:
                logging.warning(f"Full article scraping failed for: {entry.title}, using original description.")


            # Process and save new article
            pub_date = parse_pub_date(entry)
            text_without_tags = remove_html_tags(entry.description)
            processed_text = clean_text(text_without_tags)

            article_data = {
                "title": entry.title,
                "description": processed_text, # Still using description for 'short' summary if needed
                "full_text": translated_text, # Translated full article text (can be None if translation failed)
                "pub_date": pub_date.isoformat(),
                "link": entry.link,
                "category": entry.get("category", "Uncategorized")
            }
            insert_response = supabase.table("article").insert(article_data).execute()
            article_id = insert_response.data[0]['id']

            logging.info(f"New article saved with ID {article_id}: {entry.title}")

            # Publish to Pub/Sub for audio generation (including full_text)
            article_message = {
                "article_id": str(article_id),
                "title": str(entry.title),
                "description": str(processed_text),
                "full_text": str(translated_text) if translated_text else "Translation failed", # Include translated text in message
                "pub_date": pub_date.isoformat(),
                "link": str(entry.link)
            }
            future = publisher.publish(topic_path, json.dumps(article_message).encode("utf-8"))
            print(f"Message id: {future.result()}")

    return "RSS scraping and saving complete", 200
