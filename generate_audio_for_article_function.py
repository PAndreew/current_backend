import logging
import os
import random
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
    # Decode the Pub/Sub message data received from Eventarc
    article_data = json.loads(event['data'].decode("utf-8"))
    article_id = article_data.get("article_id")

    try:
        # Generate audio content using text-to-speech
        text_content = f"{article_data['title']}. {article_data['description']}"
        audio_stream = text_to_speech_stream(text_content)  # Assuming this function exists

        # Upload audio to Google Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket("your_bucket_name")
        filename = f"audios/{article_data['title']}.mp3"
        blob = bucket.blob(filename)
        blob.upload_from_file(audio_stream, content_type="audio/mpeg")
        audio_url = f"https://storage.googleapis.com/{bucket.name}/{filename}"

        # Calculate file length and duration
        blob.reload()  # Refresh metadata to get the updated size
        file_length_bytes = blob.size
        temp_audio_file = f"/tmp/{article_data['title']}.mp3"
        blob.download_to_filename(temp_audio_file)
        audio = AudioSegment.from_file(temp_audio_file)
        duration_minutes = audio.duration_seconds / 60

        # Store audio metadata in the database
        audio_data = {
            "article_id": article_id,
            "audio_url": audio_url,
            "length": file_length_bytes,
            "duration": round(duration_minutes, 2)
        }
        supabase.table("audio_file").insert(audio_data).execute()
        logging.info(f"Audio file created and saved for article ID: {article_id}")

        # Log the completion (Eventarc will trigger the next function)
        logging.info(f"Audio generated successfully for article ID {article_id}")

    except Exception as e:
        logging.error(f"Error generating audio for article ID {article_id}: {e}")
        return "Audio generation failed", 500

    return "Audio generated and saved", 200