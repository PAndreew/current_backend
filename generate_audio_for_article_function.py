import logging
import os
import requests
from google.cloud import storage
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")  # Default if not set
url = os.getenv("AUDIO_GENERATION_FUNCTION_URL")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def generate_audio_for_article(request):
    request_json = request.get_json()
    article_id = request_json.get("article_id")
    
    if not article_id:
        logging.error("Article ID not provided in the request.")
        return "Article ID missing", 400

    # Fetch the article from Supabase
    response = supabase.table("articles").select("*").eq("id", article_id).execute()
    article_data = response.data

    if not article_data:
        logging.error(f"Article with ID {article_id} not found")
        return "Article not found", 404

    # Use the first (and should be only) entry in response data
    article = article_data[0]

    # Generate audio content using ElevenLabs API
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    tts_payload = {
        "text": article['description'],
        "voice_settings": {
            "voice": "Rachel",  # Replace with the voice ID or name you wish to use
            "stability": 0.75,
            "similarity_boost": 0.85
        }
    }
    
    tts_url = "https://api.elevenlabs.io/v1/text-to-speech"  # Base URL for ElevenLabs API
    response = requests.post(tts_url, headers=headers, json=tts_payload)

    if response.status_code != 200:
        logging.error(f"ElevenLabs API error: {response.status_code} - {response.text}")
        return "Error generating audio", 500

    # Save audio to Google Cloud Storage
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f"audio/{article_id}.mp3")
    blob.upload_from_string(response.content, content_type="audio/mp3")
    audio_url = f"gs://{bucket_name}/audio/{article_id}.mp3"

    # Store the audio URL in Supabase
    audio_data = {
        "article_id": article_id,
        "audio_url": audio_url
    }
    supabase.table("audio_files").insert(audio_data).execute()

    logging.info(f"Audio file saved for article ID {article_id}: {audio_url}")
    return "Audio file generated and saved", 200
