#!/bin/sh

# Install wget if missing (debian slim)
if ! command -v wget >/dev/null 2>&1; then
  echo "[tunnel] Installing wget..."
  apt-get update -qq && apt-get install -y -qq wget ca-certificates
fi

# Install cloudflared if not present
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[tunnel] Installing cloudflared..."
  wget -qO /usr/local/bin/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x /usr/local/bin/cloudflared
fi

OUTPUT="${TUNNEL_OUTPUT:-/tmp/tunnel/url.txt}"
mkdir -p "$(dirname "$OUTPUT")"

echo "[tunnel] Starting tunnel to http://web:80"
cloudflared tunnel --url http://web:80 2>&1 | while IFS= read -r line; do
  echo "$line"
  url=$(echo "$line" | grep -o 'https://[^ ]*trycloudflare\.com')
  if [ -n "$url" ]; then
    echo "$url" > "$OUTPUT"
    echo "[tunnel] URL saved: $url"
  fi
done
