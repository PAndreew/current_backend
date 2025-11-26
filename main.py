"""
Global Markets 24/7 - News Generation Engine
Hybrid: google-genai for LLM + Gemini TTS for Audio
"""
import os
import json
import logging
import concurrent.futures
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

import feedparser
from google.cloud import storage
from google import genai
from google.genai.types import GenerateContentConfig
# from google.cloud import texttospeech
from google.api_core.client_options import ClientOptions
from google.cloud import texttospeech_v1beta1 as texttospeech
from pydub import AudioSegment

# --- Configuration ---
PROJECT_ID = os.getenv("GCP_PROJECT")
BUCKET_NAME = os.getenv("BUCKET_NAME")
MAX_WORKERS = 5

# CRITICAL: Force US-Central1 for AI models to avoid 404 errors in Europe
AI_LOCATION = "us-central1" 

# RSS Config
FEEDS = [
    "https://news.google.com/rss/search?q=stock+markets+OR+forex+when:1h&hl=en-US&gl=US&ceid=US:en",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html"
]
KEYWORDS = ["Breaking", "Spike", "Drop", "Rate", "Record", "Surge", "Plunge", "Vote"]

# --- AI Model Config ---
# LLM: Summarizes the text
LLM_MODEL_ID = "gemini-2.5-flash-lite" 

# TTS: Voices the text (The "Good" one you liked)
TTS_MODEL_ID = "gemini-2.5-flash-tts"
TTS_VOICE_NAME = "Fenrir" # Specific persona
TTS_STYLE_PROMPT = (
    "You are a Wall Street news anchor. "
    "Speak with authority. "
    "Emphasize the financial figures. "
    "Do not sound too happy, sound professional and serious."
)

ARTICLE_LANG="Hungarian"

TTS_LOCATION="global"

LANGUAGE_CODE="hu-HU"

class Article(BaseModel):
    title: str = Field(description="Title of the article.")
    article_body: str = Field(description="The body of the article.")

# --- Client Initialization ---
storage_client = storage.Client()

# 1. GenAI Client (For Text Summarization)
# Explicitly targeting us-central1
ai_client = genai.Client(
    vertexai=True, 
    project=PROJECT_ID, 
    location=AI_LOCATION
)

# 2. TTS Client (For Audio Generation)
# Explicitly targeting us-central1 endpoint for Gemini TTS availability

API_ENDPOINT = (
    f"{TTS_LOCATION}-texttospeech.googleapis.com"
    if TTS_LOCATION != "global"
    else "texttospeech.googleapis.com"
)

tts_client = texttospeech.TextToSpeechClient(
    client_options=ClientOptions(api_endpoint=API_ENDPOINT)
)

tts_voice = texttospeech.VoiceSelectionParams(
    name=TTS_VOICE_NAME, language_code=LANGUAGE_CODE, model_name=TTS_MODEL_ID
)

# tts_client_options = ClientOptions(
#     api_endpoint=f"{AI_LOCATION}-texttospeech.googleapis.com"
# )

# tts_client = texttospeech.TextToSpeechClient(client_options=tts_client_options)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_and_filter_rss() -> List[Dict[str, str]]:
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get('title', '')
                if any(k.lower() in title.lower() for k in KEYWORDS):
                    items.append({
                        'title': title,
                        'guid': entry.get('guid', entry.get('link'))
                    })
        except Exception as e:
            logger.error(f"Feed error {url}: {e}")
            continue
    return list({i['guid']: i for i in items}.values())

def manage_history(bucket, new_guids):
    blob = bucket.blob('history.json')
    history = json.loads(blob.download_as_string()) if blob.exists() else []
    valid_guids = [g for g in new_guids if g not in history]
    if valid_guids:
        blob.upload_from_string(json.dumps((history + valid_guids)[-1000:]))
    return valid_guids

def generate_script(news_item: Dict[str, str]) -> Optional[str]:
    """
    Uses google-genai SDK (Gemini 2.5 Flash Lite) to summarize.
    """
    prompt = (
        "You are a Wall Street squawk box reporter."
        f"Rewrite this headline into a concise 30-second script in {ARTICLE_LANG} for audio reading. "
        "No introductions like 'Jó napot'. Just the facts. "
        "ALWAYS spell out numbers. Your response shall only contain the article's text."
    )
    
    try:
        response = ai_client.models.generate_content(
            model=LLM_MODEL_ID,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": Article.model_json_schema(),
            },
        )
        return Article.model_validate_json(response.text).strip()
    except Exception as e:
        logger.error(f"LLM Generation Error: {e}")
        return None

def generate_audio_gemini(text: str, output_filename: str) -> bool:
    """
    Uses the Gemini TTS model with Style Prompt.
    """
    try:
        # Input with specific style prompt
        response = tts_client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text, prompt=TTS_STYLE_PROMPT),
            voice=tts_voice,
            # Select the type of audio file you want returned
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            ),
        )

        with open(output_filename, "wb") as f:
            f.write(response.audio_content)
        
        return True

    except Exception as e:
        logger.error(f"Gemini TTS Error: {e}")
        return False

def post_process_audio(voice_file: str, output_file: str):
    """
    Overlays background ticker noise using Pydub.
    """
    try:
        voice = AudioSegment.from_mp3(voice_file)
        if os.path.exists("/tmp/ticker.mp3"):
            bg = AudioSegment.from_mp3("/tmp/ticker.mp3")
            if len(bg) < len(voice):
                bg = bg * (len(voice) // len(bg) + 1)
            bg = bg[:len(voice)] - 15 # Ducking
            voice = voice.overlay(bg)
        
        voice.export(output_file, format="mp3", bitrate="192k")
        return True
    except Exception as e:
        logger.error(f"Post-process error: {e}")
        return False

def process_single_item(item, index, bucket):
    script = generate_script(item)
    if not script: return None

    temp_raw = f"/tmp/raw_{index}.mp3"
    final_mp3 = f"/tmp/news_{index:03d}.mp3"
    
    # Using the GEMINI audio function now
    if generate_audio_gemini(script, temp_raw):
        if post_process_audio(temp_raw, final_mp3):
            blob = bucket.blob(f"news/news_{index:03d}.mp3")
            blob.upload_from_filename(final_mp3)
            return f"news_{index:03d}.mp3"
    return None

def entry_point(request):
    bucket = storage_client.bucket(BUCKET_NAME)
    
    # Download background asset
    blob_bg = bucket.blob('assets/ticker_bg.mp3')
    if blob_bg.exists():
        blob_bg.download_to_filename('/tmp/ticker.mp3')
    
    raw_items = fetch_and_filter_rss()
    valid_guids = manage_history(bucket, [i['guid'] for i in raw_items])
    items = [i for i in raw_items if i['guid'] in valid_guids][:30]

    if not items: return "No new items.", 200

    # Clean bucket
    blobs = list(bucket.list_blobs(prefix="news/"))
    bucket.delete_blobs(blobs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_single_item, item, i, bucket) for i, item in enumerate(items, 1)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    count = len([r for r in results if r])
    return f"Generated {count} news items with Gemini TTS.", 200