#!/bin/bash
# ClawMeeting Uninstall Script (Mac/Linux)
# Usage: bash uninstall.sh

OPENCLAW_DIR="$HOME/.openclaw"
CONFIG_PATH="$OPENCLAW_DIR/openclaw.json"

echo ""
echo "===== ClawMeeting Uninstall ====="

# 1. Remove plugin code
EXTENSION_DIR="$OPENCLAW_DIR/extensions/clawmeeting"
if [ -d "$EXTENSION_DIR" ]; then
    rm -rf "$EXTENSION_DIR"
    echo "[OK] Removed plugin code: $EXTENSION_DIR"
else
    echo "[--] Plugin code not found: $EXTENSION_DIR"
fi

# 2. Remove runtime data
DATA_DIR="$OPENCLAW_DIR/clawmeeting"
if [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
    echo "[OK] Removed runtime data: $DATA_DIR"
else
    echo "[--] Runtime data not found: $DATA_DIR"
fi

# 3. Clean openclaw.json using node
if [ -f "$CONFIG_PATH" ]; then
    if command -v node &>/dev/null; then
        node -e '
const fs = require("fs");
const configPath = process.argv[1];
try {
    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    let changed = false;
    if (config.plugins && Array.isArray(config.plugins.allow)) {
        const before = config.plugins.allow.length;
        config.plugins.allow = config.plugins.allow.filter(p => p !== "clawmeeting");
        if (config.plugins.allow.length < before) { console.log("[OK] plugins.allow: removed clawmeeting"); changed = true; }
    }
    if (config.plugins && config.plugins.entries && config.plugins.entries.clawmeeting) {
        delete config.plugins.entries.clawmeeting;
        console.log("[OK] plugins.entries: removed clawmeeting"); changed = true;
    }
    if (config.plugins && config.plugins.installs && config.plugins.installs.clawmeeting) {
        delete config.plugins.installs.clawmeeting;
        console.log("[OK] plugins.installs: removed clawmeeting"); changed = true;
    }
    if (config.gateway && config.gateway.tools && Array.isArray(config.gateway.tools.allow)) {
        const before = config.gateway.tools.allow.length;
        config.gateway.tools.allow = config.gateway.tools.allow.filter(t => t !== "sessions_send" && t !== "message");
        if (config.gateway.tools.allow.length < before) { console.log("[OK] gateway.tools.allow: removed sessions_send, message"); changed = true; }
    }
    if (changed) {
        fs.writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");
        console.log("[OK] openclaw.json updated");
    } else {
        console.log("[--] No clawmeeting config found in openclaw.json");
    }
} catch (err) {
    console.error("[!!] Failed to clean openclaw.json:", err.message);
}
' "$CONFIG_PATH"
    else
        echo "[!!] node not found, skipping openclaw.json cleanup"
        echo "     Please manually remove clawmeeting entries from openclaw.json"
    fi
else
    echo "[--] openclaw.json not found: $CONFIG_PATH"
fi

echo ""
echo "===== Uninstall Complete ====="
echo "To reinstall: openclaw plugin install clawmeeting"
echo "Then restart: openclaw gateway restart"
echo ""
