# Runs the TitleSift page on any free container host (Hugging Face Spaces,
# Render, Koyeb, Fly). Set YOUTUBE_API_KEY as a secret on the host.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY titlesift/ titlesift/
COPY webapp/ webapp/
COPY learned_catalogue.json .
COPY dbcache/ dbcache/
ENV PORT=7860 TITLESIFT_DBCACHE=/tmp/dbcache
RUN mkdir -p /tmp/dbcache && cp dbcache/*.json /tmp/dbcache/
EXPOSE 7860
CMD ["python3", "webapp/server.py"]
