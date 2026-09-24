FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py mcp_pipe.py ./

# Render tự set PORT; mặc định khớp server.py.
ENV PORT=8080
EXPOSE 8080

# Pipe mode 24/7: mcp_pipe ket noi OUT wss api.xiaozhi.me (MCP_ENDPOINT),
# server.py chay stdio + thread nen /health + /stream tren PORT.
CMD ["python", "mcp_pipe.py", "server.py"]