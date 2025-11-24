## Prerequisites

Before deploying these functions, ensure you have the following:

*   A Google Cloud Platform (GCP) project.
*   The Google Cloud SDK (gcloud) installed and configured.
*   A Supabase project with a database.
*   An ElevenLabs account with an API key.
*   A Google Cloud Storage bucket.
*   Enabled the Google Cloud Build API.
*   Enabled the Pub/Sub API.
*   Enabled the Cloud Functions API.
*   Enabled the Cloud Storage API.

## Environment Variables

These Cloud Functions rely on the following environment variables, which need to be set within your Google Cloud Functions environment:

*   **`ELEVENLABS_API_KEY`**: Your ElevenLabs API key for text-to-speech conversion.
*   **`SUPABASE_URL`**: The URL of your Supabase project.
*   **`SUPABASE_KEY`**: The API key for your Supabase project.
*   **`GCS_BUCKET_NAME`**: The name of your Google Cloud Storage bucket. Defaults to `news_audio_bucket` if not set.
*   **`GOOGLE_CLOUD_PROJECT`**: Your Google Cloud project ID.
*   **`GOOGLE_API_KEY`**: Your Google Gemini API key for translation.
*   **`OPENAI_API_KEY`**: Your OpenAI API key (if used for alternative translation/summarization).

## Setup and Deployment

1.  **Clone the repository:**

    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **Install dependencies:**

    Navigate to each function directory (`scrape_and_save_articles`, `generate_audio_for_article`, `generate_rss_feed`) and run:

    ```bash
    pip install -r requirements.txt -t lib
    ```

3.  **Deploy Cloud Functions:**

    Deploy each function using the `gcloud functions deploy` command.  Make sure to replace `<FUNCTION_NAME>`, `<TRIGGER>`, and `<ENTRY_POINT>` with the correct values for each function.  Also, be sure to set the necessary environment variables.

    **Example for `scrape_and_save_articles`:**

    ```bash
    gcloud functions deploy scrape-and-save-articles \
    --region=us-central1 \
    --runtime=python310 \
    --trigger-http \
    --entry-point=scrape_and_save_articles \
    --set-env-vars=ELEVENLABS_API_KEY=$ELEVENLABS_API_KEY,SUPABASE_URL=$SUPABASE_URL,SUPABASE_KEY=$SUPABASE_KEY,GCS_BUCKET_NAME=$GCS_BUCKET_NAME,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_API_KEY=$GOOGLE_API_KEY,OPENAI_API_KEY=$OPENAI_API_KEY \
    --memory=256MB \
    --timeout=540
    ```

    **Important:**

    *   For `generate_audio_for_article`, the trigger is a Pub/Sub topic named `articles-saved`.  You'll need to create this topic first.
    *   For `generate_rss_feed`, the trigger is a Pub/Sub topic named `audio-generated`. You'll need to create this topic first.
    *   Adjust the `--memory` and `--timeout` values based on the needs of each function.
    *   The `--trigger-topic` parameter (when applicable) must match the Pub/Sub topic name exactly.
    *   Replace `us-central1` with your desired region.

    **Example for `generate_audio_for_article`:**

    ```bash
    gcloud functions deploy generate-audio-for-article \
    --region=us-central1 \
    --runtime=python310 \
    --trigger-topic=articles-saved \
    --entry-point=generate_audio_for_article \
    --set-env-vars=ELEVENLABS_API_KEY=$ELEVENLABS_API_KEY,SUPABASE_URL=$SUPABASE_URL,SUPABASE_KEY=$SUPABASE_KEY,GCS_BUCKET_NAME=$GCS_BUCKET_NAME,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT \
    --memory=512MB \
    --timeout=540
    ```

    **Example for `generate_rss_feed`:**

    ```bash
    gcloud functions deploy generate-rss-feed \
    --region=us-central1 \
    --runtime=python310 \
    --trigger-topic=audio-generated \
    --entry-point=generate_rss_feed \
    --set-env-vars=SUPABASE_URL=$SUPABASE_URL,SUPABASE_KEY=$SUPABASE_KEY,GCS_BUCKET_NAME=$GCS_BUCKET_NAME,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT \
    --memory=256MB \
    --timeout=540
    ```

4.  **Grant necessary permissions:**

    Ensure the Cloud Functions service accounts have the necessary permissions to access Supabase, Google Cloud Storage, and Pub/Sub.  This may involve granting roles such as:

    *   `roles/storage.objectAdmin` for GCS access
    *   `roles/pubsub.publisher` and `roles/pubsub.subscriber` for Pub/Sub access.

## Configuration

*   **RSS Feeds:**  Modify the `RSS_FEEDS` list in `scrape_and_save_articles.py` to include the RSS feeds you want to scrape.
*   **Supabase Tables:**  Ensure you have the necessary tables in your Supabase database. The code assumes tables named `article` and `audio_file` with appropriate columns.
*   **ElevenLabs Voice ID:** Update the `audio_ids` tuple in `generate_audio_for_article.py` with the desired ElevenLabs voice IDs.
*   **Podcast Info:** Update `fetch_podcast_info` function on the `generate_rss_feed.py` file with your podcast information and the ID on the Supabase `Podcast` table.

## Testing

*   **`scrape_and_save_articles`:** Invoke this function via HTTP to trigger the scraping and saving process.
*   **`generate_audio_for_article`:** Send a test message to the `articles-saved` Pub/Sub topic.
*   **`generate_rss_feed`:** Send a test message to the `audio-generated` Pub/Sub topic.  Then, check your GCS bucket for the generated RSS feed file.

## Troubleshooting

*   Check the Cloud Functions logs for errors.
*   Verify that the environment variables are set correctly.
*   Ensure that the service accounts have the necessary permissions.
*   Test each function independently to isolate issues.

## License

[MIT License](LICENSE)
