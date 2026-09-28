FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates unzip git \
    && curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
        -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && deno --version \
    && rm -f /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*

# bgutil PO-token server — giai phap lau dai cho YouTube bot-check
# ("Sign in to confirm you're not a bot") — khong con phu thuoc cookies
# export lai moi ngay. Version PHAI khop plugin pip trong requirements.txt
# (bgutil-ytdlp-pot-provider==2.0.0): yt-dlp doi version qua GET /ping.
ARG BGUTIL_VERSION=2.0.0
RUN git clone --depth 1 --single-branch --branch ${BGUTIL_VERSION} \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil
WORKDIR /opt/bgutil/server
ENV DENO_NO_PROMPT=1 DENO_NO_UPDATE_CHECK=1 DENO_DIR=/opt/bgutil/server/.cache/deno
# Giong deno flavor trong Dockerfile cua bgutil: cai deps + prefetch module.
RUN deno install --allow-scripts=npm:canvas --frozen \
    && deno cache --frozen src/main.ts

WORKDIR /app
COPY tools/zing-music-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/zing-music-mcp/server.py tools/zing-music-mcp/mcp_pipe.py \
     tools/zing-music-mcp/start.sh ./

# Render tự set PORT; mặc định khớp server.py.
ENV PORT=8080
EXPOSE 8080

# Pipe mode 24/7: mcp_pipe ket noi OUT wss api.xiaozhi.me (MCP_ENDPOINT),
# server.py chay stdio + thread nen /health + /stream tren PORT.
# start.sh: boot bgutil POT server (127.0.0.1:4416) truoc khi chay mcp_pipe.
CMD ["/bin/sh", "start.sh"]