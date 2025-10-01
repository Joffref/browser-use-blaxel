#!/bin/sh

# Chrome for Testing entrypoint script for Unikraft environments
# Chrome for Testing is designed for automation and has relaxed security restrictions

# Start Chrome in the background with DevTools Protocol
echo "🚀 Starting Chrome with DevTools Protocol..."
chromium-browser \
  `# === CORE HEADLESS FLAGS ===` \
  --headless \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-dev-session \
  `# === SECURITY & SANDBOXING ===` \
  --no-sandbox \
  --disable-web-security \
  --disable-extensions \
  --disable-plugins \
  --disable-default-apps \
  `# === MEMORY & SHARED RESOURCES ===` \
  --disable-dev-shm-usage \
  --memory-pressure-off \
  --max_old_space_size=4096 \
  `# === REMOTE DEBUGGING CONFIGURATION ===` \
  --remote-allow-origins=* \
  --allow-insecure-localhost \
  --disable-features=VizNetworkService,VizDisplayCompositor,TranslateUI \
  `# === RENDERING & GPU ===` \
  --disable-gpu \
  `# === MEDIA & HARDWARE (if audio not needed) ===` \
  --mute-audio \
  --use-fake-device-for-media-stream \
  --v=1 \
  "$@" &

CHROME_PID=$!

# Start nginx proxy in the foreground to handle host header mapping
echo "🔄 Starting nginx proxy..."
exec nginx -g "daemon off;"