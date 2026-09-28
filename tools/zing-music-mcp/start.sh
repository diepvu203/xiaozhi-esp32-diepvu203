#!/bin/sh
# Boot bgutil PO-token server (127.0.0.1:4416) truoc khi chay mcp_pipe.
# Plugin yt-dlp bgutil:http (pip requirements.txt) tu discover server nay
# de lay PO token vuot YouTube bot-check — khong con phu thuoc cookies.
# POT that bai van khong chan mcp_pipe: chi ghi warning (yt-dlp van chay
# khong PO token nhu truoc day).
set -u

POT_DIR=/opt/bgutil/server
cd "$POT_DIR" || { echo "[start] FATAL: khong tim thay $POT_DIR" >&2; exit 1; }

deno run \
    --allow-env --allow-net \
    --allow-ffi="$POT_DIR/node_modules" \
    --allow-read="$POT_DIR/node_modules" \
    "$POT_DIR/src/main.ts" &
POT_PID=$!

# Cho server len /ping (plugin chi cho 5s moi ping) — toi da 15s.
i=0
while [ "$i" -lt 30 ]; do
    if curl -fsS http://127.0.0.1:4416/ping >/dev/null 2>&1; then
        echo "[start] bgutil POT server ready (pid $POT_PID)"
        break
    fi
    i=$((i + 1))
    sleep 0.5
done
if [ "$i" -eq 30 ]; then
    echo "[start] WARNING: bgutil POT server khong ping duoc /ping — yt-dlp se khong co PO token" >&2
fi

exec python mcp_pipe.py server.py