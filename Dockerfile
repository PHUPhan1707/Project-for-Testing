FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY scraper.py gemini_auth.py gemini_upload_to_store.py main.py ./

# Run the daily scrape + delta-upload job once, then exit.
# The job reads GEMINI_API_KEY (or GOOGLE_API_KEY / API_KEY) from the env.
ENTRYPOINT ["python", "main.py"]
