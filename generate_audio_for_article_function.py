import logging
import os
import json
import re
import random
import base64
import unicodedata
from urllib.parse import quote
from io import BytesIO
from supabase import create_client, Client
from google.cloud import storage, pubsub_v1
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from pydub import AudioSegment

# Initialize Supabase and ElevenLabs clients
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
audio_ids = ("lVCldLIMCFckDUbGfwtx", "lVCldLIMCFckDUbGfwtx")

def text_to_speech_stream(text: str) -> BytesIO:
    # Perform the text-to-speech conversion with ElevenLabs
    response = client.text_to_speech.convert(
        voice_id=random.choice(audio_ids),  # Replace with actual voice ID
        output_format="mp3_22050_32",
        text=text,
        model_id="eleven_turbo_v2_5",
        voice_settings=VoiceSettings(
            stability=0.0,
            similarity_boost=1.0,
            style=0.0,
            use_speaker_boost=True,
        ),
    )

    # Create a BytesIO object to hold the audio data in memory
    audio_stream = BytesIO()

    # Write each chunk of audio data to the stream
    for chunk in response:
        if chunk:
            audio_stream.write(chunk)

    # Reset stream position to the beginning
    audio_stream.seek(0)
    return audio_stream

def generate_audio_for_article(event, context):
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path("currentlyai", "audio-generated")
    print(event['data'])

    decoded_data = base64.b64decode(event['data']).decode("utf-8")
    article_data = json.loads(decoded_data)
    article_id = article_data["article_id"]

    # Check if audio already exists for this article
    existing_audio = supabase.table("audio_file").select("*").eq("article_id", article_id).execute()
    if existing_audio.data:
        logging.info(f"Audio already exists for article ID {article_id}; skipping generation.")
        return "Audio already exists; skipping generation", 200

    # Generate audio content
    try:
        existing_audio = supabase.table("audio_file").select("*").eq("article_id", article_id).execute()
        if existing_audio.data:
            logging.info(f"Audio for article ID {article_id} already exists. Skipping generation.")
            return "Audio already exists", 200
        text_content = f"{article_data['title']}. {article_data['description']}"
        audio_stream = text_to_speech_stream(text_content)  # Audio generation logic

        # Upload audio to GCS
        storage_client = storage.Client()
        bucket = storage_client.bucket("news_audio_bucket")
        # Normalize and sanitize title for filename
        safe_title = unicodedata.normalize('NFD', article_data['title'])
        safe_title = ''.join(c for c in safe_title if unicodedata.category(c) != 'Mn')  # Strip accents
        safe_title = re.sub(r'[^\w\s-]', '', safe_title)  # Remove special characters
        safe_title = safe_title.replace(' ', '_')  # Replace spaces with underscores

        # (Optional) Encode for URL safety
        safe_title = quote(safe_title)
        filename = f"audios/{safe_title}.mp3"
        blob = bucket.blob(filename)
        blob.upload_from_file(audio_stream, content_type="audio/mpeg")
        audio_url = f"https://storage.googleapis.com/{bucket.name}/{filename}"

        # Calculate duration and size
        blob.reload()
        file_length_bytes = blob.size
        temp_audio_file = f"/tmp/{article_data['title']}.mp3"
        blob.download_to_filename(temp_audio_file)
        audio = AudioSegment.from_file(temp_audio_file)
        duration_minutes = audio.duration_seconds / 60

        # Save audio data to the database
        audio_data = {
            "article_id": article_id,
            "audio_url": audio_url,
            "length": file_length_bytes,
            "duration": round(duration_minutes, 2)
        }
        supabase.table("audio_file").insert(audio_data).execute()

        # Publish to `audio-generated` Pub/Sub topic
        audio_message = {
            "article_id": str(article_id),
            "audio_url": str(audio_url),
            "length": file_length_bytes,
            "duration": round(duration_minutes, 2)
        }
        future = publisher.publish(topic_path, json.dumps(audio_message).encode("utf-8"))
        print(f"Message id: {future.result()}")
        
        logging.info(f"Audio generated for article ID {article_id}")

    except Exception as e:
        logging.error(f"Error generating audio for article {article_id}: {e}")
        return "Audio generation failed", 500

    return "Audio generated and saved", 200
