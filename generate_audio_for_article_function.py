import logging
import json
import os
from google.cloud import texttospeech as tts
from google.cloud import storage
from supabase import create_client, Client

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
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

    # Generate audio content using Google Text-to-Speech
    tts_client = tts.TextToSpeechClient()
    synthesis_input = tts.SynthesisInput(text=article['description'])
    voice = tts.VoiceSelectionParams(language_code="en-US", ssml_gender=tts.SsmlVoiceGender.NEUTRAL)
    audio_config = tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3)

    response = tts_client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)

    # Save audio to Google Cloud Storage
    storage_client = storage.Client()
    bucket_name = "news_audio_bucket"  # Replace with your actual bucket name
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f"audio/{article_id}.mp3")
    blob.upload_from_string(response.audio_content, content_type="audio/mp3")
    audio_url = f"gs://{bucket_name}/audio/{article_id}.mp3"

    # Store the audio URL in Supabase
    audio_data = {
        "article_id": article_id,
        "audio_url": audio_url
    }
    supabase.table("audio_files").insert(audio_data).execute()

    logging.info(f"Audio file saved for article ID {article_id}: {audio_url}")
    return "Audio file generated and saved", 200
