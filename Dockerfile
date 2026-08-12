# Use the official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Cloud Run logs
ENV PYTHONUNBUFFERED True

# Set the working directory
ENV APP_HOME /app
WORKDIR $APP_HOME

# Install system dependencies (MoviePy requires ffmpeg for video processing)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy local code to the container image
COPY . ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the web service on container startup using Uvicorn.
# Cloud Run injects the PORT environment variable (defaults to 8080)
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
