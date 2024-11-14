from supabase import create_client, Client
import xml.etree.ElementTree as ET
from google.cloud import pubsub_v1, storage
from datetime import datetime
import json
import base64

# Initialize Supabase client
SUPABASE_URL=r"https://uhhdiibmeitulvkbpwud.supabase.co"
SUPABASE_ANON_KEY=r"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVoaGRpaWJtZWl0dWx2a2Jwd3VkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzA4MzA5NjQsImV4cCI6MjA0NjQwNjk2NH0.f-mcqw7Pc6IUIthL4kGgUYdRWXEpgPyWQrWYcFx2MXs"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def fetch_podcast_info(podcast_id):
    # Fetch podcast metadata from Supabase
    response = supabase.table("podcast").select("*").eq("id", podcast_id).single().execute()
    if response.data:
        return response.data
    else:
        print("Error fetching podcast data:", response)
        return None

def fetch_episodes_with_audio(podcast_category):
    # Fetch articles and their associated audio files
    response = (
        supabase.table("article")
        .select("*, audio_file(url, length)")
        .eq("category", podcast_category)
        .execute()
    )
    if response.data:
        return response.data
    else:
        print("Error fetching articles and audio files:", response)
        return []

def generate_rss_feed(event, context):
    # Decode the Pub/Sub message
    decoded_data = base64.b64decode(event['data']).decode("utf-8")
    message_data = json.loads(decoded_data)
    podcast_id = message_data.get("podcast_id")  # Assuming `podcast_id` is passed in the Pub/Sub message

    # Fetch podcast and episode data from Supabase
    podcast_info = fetch_podcast_info(podcast_id)
    if not podcast_info:
        print("Missing podcast data; RSS feed generation aborted.")
        return

    # Fetch all episodes with audio for this podcast
    episodes = fetch_episodes_with_audio(podcast_info["category"])

    if not episodes:
        print("No episodes found; RSS feed generation aborted.")
        return

    # Start generating the RSS feed
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    # Podcast-level tags
    ET.SubElement(channel, "title").text = podcast_info["title"]
    ET.SubElement(channel, "link").text = podcast_info["homepage_url"]
    ET.SubElement(channel, "description").text = podcast_info["description"]
    
    # Podcast image
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = podcast_info["image_url"]
    ET.SubElement(image, "title").text = podcast_info["title"]
    ET.SubElement(image, "link").text = podcast_info["homepage_url"]
    
    # iTunes-specific tags
    ET.SubElement(channel, "itunes:image", href=podcast_info["image_url"])
    ET.SubElement(channel, "itunes:author").text = podcast_info["author"]
    ET.SubElement(channel, "itunes:explicit").text = "yes" if podcast_info["explicit"] else "no"
    ET.SubElement(channel, "language").text = podcast_info["language"]
    owner = ET.SubElement(channel, "itunes:owner")
    ET.SubElement(owner, "itunes:email").text = podcast_info["owner_email"]
    
    # Category and other optional tags
    ET.SubElement(channel, "itunes:category", text=podcast_info["category"])

    # Episode-level tags
    for episode in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode["title"]
        ET.SubElement(item, "description").text = episode.get("description", "")

        # Enclosure with URL, type, and length from audio_file
        if "audio_file" in episode:
            audio_url = episode["audio_file"][0]["audio_url"]
            audio_length = episode["audio_file"][0]["length"]
            ET.SubElement(item, "enclosure", url=audio_url, type="audio/mpeg", length=str(audio_length))

        # GUID and publication date
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = audio_url  # Use audio URL as GUID
        pub_date = episode.get("pub_date", datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'))
        ET.SubElement(item, "pubDate").text = pub_date

        # Duration (assuming duration is available)
        if "duration" in episode["audio_file"]:
            ET.SubElement(item, "itunes:duration").text = str(round(episode["audio_file"]["duration"], 2))
        
        # Explicit flag for episode
        ET.SubElement(item, "itunes:explicit").text = "yes" if episode.get("explicit", False) else "no"
        
    # Write the RSS feed to a local XML file
    local_file_path = "/tmp/podcast_feed.xml"
    tree = ET.ElementTree(rss)
    tree.write(local_file_path, encoding='utf-8', xml_declaration=True)
    print("RSS feed generated locally.")

    # Upload the XML file to Google Cloud Storage at a fixed URL
    storage_client = storage.Client()
    bucket = storage_client.bucket("news_audio_bucket")
    blob = bucket.blob("audio/rss/rss_feed.xml")
    blob.upload_from_filename(local_file_path, content_type="application/rss+xml")
    print("RSS feed uploaded to Google Cloud Storage.")

# Usage
# generate_rss_feed("your-podcast-id")
