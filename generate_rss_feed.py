from supabase import create_client, Client
import xml.etree.ElementTree as ET
from google.cloud import pubsub_v1, storage
from email.utils import format_datetime
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

def fetch_episodes_with_audio():
    # Fetch articles and their associated audio files
    response = (
        supabase.table("article")
        .select("*, audio_file(audio_url, length, duration)")
        .execute()
    )
    if response.data:
        return response.data
    else:
        print("Error fetching articles and audio files:", response)
        return []

def generate_rss_feed(event, context):
    # Decode the Pub/Sub message
    # decoded_data = base64.b64decode(event['data']).decode("utf-8")
    # message_data = json.loads(decoded_data)
    # podcast_id = message_data.get("podcast_id")  # Assuming `podcast_id` is passed in the Pub/Sub message

    # Fetch podcast and episode data from Supabase
    podcast_info = fetch_podcast_info('76f55288-cd16-4b2c-892a-89e1aeac5b27')
    if not podcast_info:
        print("Missing podcast data; RSS feed generation aborted.")
        return

    # Fetch all episodes with audio for this podcast
    episodes = fetch_episodes_with_audio()

    if not episodes:
        print("No episodes found; RSS feed generation aborted.")
        return

    # Start generating the RSS feed
    rss = ET.Element(
        "rss", 
        version="2.0", 
        attrib={
            "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
            "xmlns:podcast": "https://podcastindex.org/namespace/1.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
            "xmlns:content": "http://purl.org/rss/1.0/modules/content/"
        }
    )
    channel = ET.SubElement(rss, "channel")
    
    # Podcast-level tags
    ET.SubElement(channel, "title").text = podcast_info["title"]
    ET.SubElement(channel, "link").text = podcast_info["homepage_url"]
    ET.SubElement(channel, "description").text = podcast_info["description"]
    ET.SubElement(channel, "language").text = podcast_info["language"]

    # Atom self-link
    ET.SubElement(channel, "atom:link", href="https://storage.googleapis.com/news_audio_bucket/audio/rss/rss_feed.xml", rel="self", type="application/rss+xml")
    
    # Podcast image
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = podcast_info["image_url"]
    ET.SubElement(image, "title").text = podcast_info["title"]
    ET.SubElement(image, "link").text = podcast_info["homepage_url"]
    
    # iTunes-specific tags
    ET.SubElement(channel, "itunes:image", href=podcast_info["image_url"])
    ET.SubElement(channel, "itunes:author").text = podcast_info["author"]
    ET.SubElement(channel, "itunes:explicit").text = "true" if podcast_info["explicit"] else "false"
    owner = ET.SubElement(channel, "itunes:owner")
    ET.SubElement(owner, "itunes:email").text = podcast_info["owner_email"]
    
    # Category and other optional tags
    ET.SubElement(channel, "itunes:category", text="News")

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
        # Convert ISO 8601 to datetime object
        iso_date = episode.get("pub_date", datetime.now(datetime.timezone.utc).isoformat())
        pub_date_obj = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))

        # Convert datetime to RFC 2822 format
        formatted_pub_date = format_datetime(pub_date_obj)

        # Add pubDate to the RSS item
        ET.SubElement(item, "pubDate").text = formatted_pub_date

         # Additional fields
        ET.SubElement(item, "link").text = episode.get("link", podcast_info["homepage_url"])  # Default to homepage if no episode link
        ET.SubElement(item, "itunes:image", href=episode.get("image_url", podcast_info["image_url"]))
        ET.SubElement(item, "itunes:explicit").text = "true" if episode.get("explicit", False) else "false"

        # Duration (assuming duration is available)
        duration = episode["audio_file"].get("duration", 0)
        ET.SubElement(item, "itunes:duration").text = str(round(duration, 2)) if duration else "0:00"
        
        
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
