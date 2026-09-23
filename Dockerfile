FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY tools/zing-music-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/zing-music-mcp/server.py ./

# Render tự set PORT; mặc định khớp server.py.
ENV PORT=8080
EXPOSE 8080

CMD ["python", "server.py"]