import logging
import os
from io import BytesIO
from supabase import create_client, Client
from google.cloud import storage
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

# Initialize Supabase and ElevenLabs clients
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
bucket_name = os.getenv("GCS_BUCKET_NAME", "news_audio_bucket")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

def text_to_speech_stream(text: str) -> BytesIO:
    # Perform the text-to-speech conversion with ElevenLabs
    response = client.text_to_speech.convert(
        voice_id="CwhRBWXzGAHq8TQ4Fs17",  # Replace with actual voice ID
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

def generate_audio_for_article(request):
    request_json = request.get_json()
    article_id = request_json.get("article_id")
    
    if not article_id:
        logging.error("Article ID not provided in the request.")
        return "Article ID missing", 400

    # Fetch the article from Supabase
    response = supabase.table("article").select("*").eq("id", article_id).execute()
    article_data = response.data

    if not article_data:
        logging.error(f"Article with ID {article_id} not found")
        return "Article not found", 404

    article = article_data[0]

    text_content = f"{article['title']}. {article['description']}"

    # Generate audio content using streaming from ElevenLabs API
    audio_stream = text_to_speech_stream(text_content)

    # Upload the audio stream directly to Google Cloud Storage
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    filename = f"audios/{article['title']}.mp3"
    blob = bucket.blob(filename)
    blob.upload_from_file(audio_stream, content_type="audio/mpeg")
    audio_url = f"https://storage.googleapis.com/{bucket_name}/{filename}"

    # Store the audio URL in Supabase
    audio_data = {
        "article_id": article_id,
        "audio_url": audio_url
    }
    supabase.table("audio_file").insert(audio_data).execute()

    logging.info(f"Audio file saved for article ID {article_id}: {audio_url}")
    return "Audio file generated and saved", 200
