#!/bin/sh
set -e

dir="$HOME/.nanobot"
config_file="$dir/config.json"

# Build comma-separated JSON arrays from env vars (split on ",")
to_json_array() {
  echo "$1" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sed 's/.*/"&"/' | paste -sd',' | sed 's/^/[/;s/$/]/'
}

ALLOW_FROM_JSON=$(to_json_array "${DISCORD_ALLOW_FROM:-*}")
ALLOW_CHANNELS_JSON=$(to_json_array "${DISCORD_ALLOW_CHANNELS:-}")

# Generate config.json from Railway env vars on first boot
if [ ! -f "$config_file" ]; then
    mkdir -p "$dir"
    cat > "$config_file" <<EOF
{
  "providers": {
    "custom": {
      "apiBase": "$OAI_url",
      "apiKey": "$OAI_key"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Primary",
      "provider": "custom",
      "model": "$OAI_model"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  },
  "gateway": {
    "host": "0.0.0.0"
  },
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "tokenIssueSecret": "${WEBSOCKET_SECRET:-nanobot-default}"
    },
    "discord": {
      "enabled": true,
      "token": "$DISCORD_BOT_TOKEN",
      "allowFrom": $ALLOW_FROM_JSON,
      "allowChannels": $ALLOW_CHANNELS_JSON,
      "groupPolicy": "open",
      "streaming": true
    }
  }
}
EOF
    echo "Generated config.json from environment variables."
fi

if [ -d "$dir" ] && [ ! -w "$dir" ]; then
    # Attempt to fix permissions if writable by root
    if [ -w "/" ]; then
        echo "Attempting to fix permissions for $dir..."
    chown -R $(id -u):$(id -g) "$dir"
    fi
    owner_uid=$(stat -c %u "$dir" 2>/dev/null || stat -f %u "$dir" 2>/dev/null)
    cat >&2 <<EOF
Error: $dir is not writable (owned by UID $owner_uid, running as UID $(id -u)).

Fix (pick one):
  Host:   sudo chown -R 1000:1000 ~/.nanobot
  Docker: docker run --user \$(id -u):\$(id -g) ...
  Podman: podman run --userns=keep-id ...
EOF
    exit 1
fi

exec nanobot "$@"
