#!/bin/sh
# Extract cloudflared tunnel URL and save to shared volume
cloudflared tunnel --url http://web:5000 2>&1 | while IFS= read -r line; do
  echo "$line"
  url=$(echo "$line" | grep -o 'https://[^ ]*trycloudflare\.com')
  if [ -n "$url" ]; then
    echo "$url" > /home/nonroot/url.txt
  fi
done
