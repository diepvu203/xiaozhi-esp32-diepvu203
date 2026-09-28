#!/bin/sh
# Boot bgutil PO-token server (127.0.0.1:4416) truoc khi chay mcp_pipe.
# Plugin yt-dlp bgutil:http (pip requirements.txt) tu discover server nay
# de lay PO token vuot YouTube bot-check - khong con phu thuoc cookies.
# POT that bai van khong chan mcp_pipe: chi ghi warning (yt-dlp van chay
# khong PO token nhu truoc day).
set -u

APP_DIR=/app
POT_DIR=/opt/bgutil/server

# Deno can cwd = server dir (tim deno.json + node_modules); sau do PHAI cd
# ve $APP_DIR truoc khi exec python (mcp_pipe.py/server.py nam o /app ->
# loi "can't open file '/opt/bgutil/server/mcp_pipe.py'").
cd "$POT_DIR" || { echo "[start] FATAL: khong tim thay $POT_DIR" >&2; exit 1; }

deno run \
    --allow-env --allow-net \
    --allow-ffi="$POT_DIR/node_modules" \
    --allow-read="$POT_DIR/node_modules" \
    "$POT_DIR/src/main.ts" &
POT_PID=$!

# Cho server len /ping toi da 60s (module graph jsdom/canvas chay cham tren
# CPU free tier); moi lan curl toi da 2s. Loi that duoc in de debug Render.
i=0
POT_READY=0
POT_ERR=""
while [ "$i" -lt 60 ]; do
    if POT_ERR=$(curl -fsS --max-time 2 --noproxy '*' http://127.0.0.1:4416/ping 2>&1); then
        POT_READY=1
        break
    fi
    # Deno chet som -> khong can doi het 60s.
    if ! kill -0 "$POT_PID" 2>/dev/null; then
        POT_ERR="deno da thoat som: $POT_ERR"
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$POT_READY" -eq 1 ]; then
    echo "[start] bgutil POT server ready (pid $POT_PID, ~${i}s): $POT_ERR"
else
    echo "[start] WARNING: bgutil POT server khong len /ping (~${i}s) - yt-dlp se khong co PO token; ${POT_ERR}" >&2
fi

cd "$APP_DIR" || { echo "[start] FATAL: khong tim thay $APP_DIR" >&2; exit 1; }
exec python mcp_pipe.py server.py