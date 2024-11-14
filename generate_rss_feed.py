from supabase import create_client, Client
import xml.etree.ElementTree as ET
from google.cloud import pubsub_v1, storage
from datetime import datetime
import json
import logging
import os
import base64
from xml.dom import minidom
import html

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_podcast_info(podcast_id):
    response = supabase.table("podcast").select("*").eq("id", podcast_id).single().execute()
    if response.data:
        return response.data
    else:
        print("Error fetching podcast data:", response)
        return None

def fetch_episodes_with_audio():
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
    decoded_data = base64.b64decode(event['data']).decode("utf-8")
    message_data = json.loads(decoded_data)
    
    # Fetch podcast and episode data from Supabase
    podcast_info = fetch_podcast_info('76f55288-cd16-4b2c-892a-89e1aeac5b27')
    if not podcast_info:
        logging.error("Missing podcast data; RSS feed generation aborted.")
        return

    episodes = fetch_episodes_with_audio()
    if not episodes:
        logging.info("No episodes found; RSS feed generation aborted.")
        return

    # Start generating the RSS feed with namespaces
    namespaces = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "atom": "http://www.w3.org/2005/Atom"
    }
    rss = ET.Element("rss", version="2.0", nsmap=namespaces)
    
    # Register namespaces explicitly
    ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    
    channel = ET.SubElement(rss, "channel")
    
    # Podcast-level tags
    ET.SubElement(channel, "title").text = html.escape(podcast_info["title"])
    ET.SubElement(channel, "link").text = html.escape(podcast_info["homepage_url"])
    ET.SubElement(channel, "description").text = html.escape(podcast_info["description"])
    
    # Podcast image
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = html.escape(podcast_info["image_url"])
    ET.SubElement(image, "title").text = html.escape(podcast_info["title"])
    ET.SubElement(image, "link").text = html.escape(podcast_info["homepage_url"])
    
    # iTunes-specific tags
    ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}image", href=html.escape(podcast_info["image_url"]))
    ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}author").text = html.escape(podcast_info["author"])
    ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit").text = "yes" if podcast_info["explicit"] else "no"
    ET.SubElement(channel, "language").text = podcast_info["language"]
    owner = ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}owner")
    ET.SubElement(owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}email").text = podcast_info["owner_email"]
    
    # Category and other optional tags
    ET.SubElement(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}category", text=podcast_info["category"])

    # Episode-level tags
    for episode in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = html.escape(episode["title"])
        ET.SubElement(item, "description").text = html.escape(episode.get("description", ""))

        # Enclosure with URL, type, and length from audio_file
        if "audio_file" in episode:
            audio_url = episode["audio_file"][0]["audio_url"]
            audio_length = episode["audio_file"][0]["length"]
            ET.SubElement(item, "enclosure", url=html.escape(audio_url), type="audio/mpeg", length=str(audio_length))

        # GUID and publication date
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = html.escape(audio_url)
        pub_date = episode.get("pub_date", datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'))
        ET.SubElement(item, "pubDate").text = pub_date

        # Duration (assuming duration is available)
        if "duration" in episode["audio_file"]:
            ET.SubElement(item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration").text = str(round(episode["audio_file"]["duration"], 2))
        
        # Explicit flag for episode
        ET.SubElement(item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit").text = "yes" if episode.get("explicit", False) else "no"
        
    # Convert the ElementTree to a pretty-printed XML string
    rough_string = ET.tostring(rss, encoding='utf-8', method='xml')
    reparsed = minidom.parseString(rough_string)
    pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8")

    # Write the pretty-printed XML to file
    local_file_path = "/tmp/podcast_feed.xml"
    with open(local_file_path, "wb") as f:
        f.write(pretty_xml)
    logging.info("RSS feed generated locally.")

    # Upload the XML file to Google Cloud Storage at a fixed URL
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket("news_audio_bucket")
        blob = bucket.blob("audio/rss/rss_feed.xml")
        blob.upload_from_filename(local_file_path, content_type="application/rss+xml")
        logging.info("RSS feed uploaded to Google Cloud Storage.")
    except Exception as e:
        logging.error(f"Error uploading RSS feed to Google Cloud Storage: {e}")