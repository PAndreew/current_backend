from supabase import create_client, Client
import xml.etree.ElementTree as ET
from google.cloud import pubsub_v1, storage
from datetime import datetime, timedelta
import json
import logging
import os
import base64
from xml.dom import minidom

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

def fetch_recent_episodes_with_audio():
    # Calculate timestamp for 24 hours ago
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    timestamp = twenty_four_hours_ago.isoformat()

    response = (
        supabase.table("article")
        .select("*, audio_file(audio_url, length, duration)")
        .gte("created_at", timestamp)  # Filter for articles newer than 24 hours
        .order("created_at", desc=True)  # Most recent first
        .execute()
    )
    
    if response.data:
        return response.data
    else:
        print("Error fetching articles and audio files:", response)
        return []

def create_xml_element(parent, tag, text=None, attrib=None):
    """Helper function to create XML elements with proper encoding"""
    element = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        element.text = text
    return element

def generate_rss_feed(event, context):
    
    # Fetch podcast and episode data from Supabase
    podcast_info = fetch_podcast_info('76f55288-cd16-4b2c-892a-89e1aeac5b27')
    if not podcast_info:
        logging.error("Missing podcast data; RSS feed generation aborted.")
        return

    # Fetch only recent episodes
    episodes = fetch_recent_episodes_with_audio()
    if not episodes:
        logging.info("No recent episodes found; RSS feed generation aborted.")
        return

    logging.info(f"Found {len(episodes)} episodes from the last 24 hours")

    # Create XML document with proper encoding declaration
    rss = ET.Element("rss", version="2.0")
    
    # Register namespaces explicitly
    ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    
    channel = ET.SubElement(rss, "channel")
    
    # Podcast-level tags with proper encoding
    create_xml_element(channel, "title", podcast_info["title"])
    create_xml_element(channel, "link", podcast_info["homepage_url"])
    create_xml_element(channel, "description", podcast_info["description"])
    
    # Add last build date
    create_xml_element(channel, "lastBuildDate", 
                      datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'))
    
    # Podcast image
    image = create_xml_element(channel, "image")
    create_xml_element(image, "url", podcast_info["image_url"])
    create_xml_element(image, "title", podcast_info["title"])
    create_xml_element(image, "link", podcast_info["homepage_url"])
    
    # iTunes-specific tags
    create_xml_element(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}image", 
                      attrib={"href": podcast_info["image_url"]})
    create_xml_element(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}author", 
                      podcast_info["author"])
    create_xml_element(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit", 
                      "yes" if podcast_info["explicit"] else "no")
    create_xml_element(channel, "language", podcast_info["language"])
    
    owner = create_xml_element(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}owner")
    create_xml_element(owner, "{http://www.itunes.com/dtds/podcast-1.0.dtd}email", 
                      podcast_info["owner_email"])
    
    create_xml_element(channel, "{http://www.itunes.com/dtds/podcast-1.0.dtd}category", 
                      attrib={"text": podcast_info["category"]})

    # Episode-level tags
    for episode in episodes:
        item = create_xml_element(channel, "item")
        create_xml_element(item, "title", episode["title"])
        create_xml_element(item, "description", episode.get("description", ""))

        # Enclosure with URL, type, and length from audio_file
        if "audio_file" in episode and episode["audio_file"]:
            audio_url = episode["audio_file"][0]["audio_url"]
            audio_length = episode["audio_file"][0]["length"]
            create_xml_element(item, "enclosure", attrib={
                "url": audio_url,
                "type": "audio/mpeg",
                "length": str(audio_length)
            })

            # GUID and publication date
            create_xml_element(item, "guid", audio_url, {"isPermaLink": "false"})
            
            # Use created_at for pubDate if available, otherwise current time
            pub_date = episode.get("created_at", datetime.utcnow().isoformat())
            # Convert ISO format to RFC 822 format required by RSS
            try:
                pub_datetime = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                formatted_pub_date = pub_datetime.strftime('%a, %d %b %Y %H:%M:%S GMT')
            except (ValueError, AttributeError):
                formatted_pub_date = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
            
            create_xml_element(item, "pubDate", formatted_pub_date)

            # Duration
            if "duration" in episode["audio_file"][0]:
                create_xml_element(item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration",
                                str(round(episode["audio_file"][0]["duration"], 2)))
        
        # Explicit flag for episode
        create_xml_element(item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit",
                         "yes" if episode.get("explicit", False) else "no")
    
    # Convert to string with proper XML declaration and encoding
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    rough_string = ET.tostring(rss, encoding='unicode', method='xml')
    reparsed = minidom.parseString(rough_string)
    pretty_xml = xml_declaration + reparsed.toprettyxml(indent="  ", encoding=None)

    # Write the pretty-printed XML to file with UTF-8 encoding
    local_file_path = "/tmp/podcast_feed.xml"
    with open(local_file_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)
    logging.info("RSS feed generated locally.")

    # Upload the XML file to Google Cloud Storage with proper content type and encoding
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket("news_audio_bucket")
        blob = bucket.blob("audio/rss/rss_feed.xml")
        blob.upload_from_filename(
            local_file_path,
            content_type="application/rss+xml; charset=utf-8"
        )
        logging.info("RSS feed uploaded to Google Cloud Storage.")
    except Exception as e:
        logging.error(f"Error uploading RSS feed to Google Cloud Storage: {e}")