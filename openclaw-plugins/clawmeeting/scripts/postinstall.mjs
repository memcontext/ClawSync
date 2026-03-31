#!/usr/bin/env node
// ClawMeeting postinstall script
// Ensures required system tools are in gateway.tools.allow in openclaw.json
// Also cleans up any plugin tool names that were incorrectly added by older versions

import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const PLUGIN_ID = "clawmeeting";

// System-level gateway tools required for push notifications
const REQUIRED_GATEWAY_TOOLS = ["sessions_send", "message"];

// Plugin tool names that should NEVER be in gateway.tools.allow or tools.allow
// (they are registered via api.registerTool() and exposed to the LLM automatically)
const PLUGIN_TOOLS = [
  "bind_identity",
  "verify_email_code",
  "initiate_meeting",
  "check_and_respond_tasks",
  "list_meetings",
];

const configPath = join(homedir(), ".openclaw", "openclaw.json");

if (!existsSync(configPath)) {
  console.log("[ClawMeeting] openclaw.json not found, skipping auto-config.");
  process.exit(0);
}

try {
  const raw = readFileSync(configPath, "utf-8");
  const config = JSON.parse(raw);
  let changed = false;

  // ── 1. plugins.allow ────────────────────────────────────────────────────────
  if (!config.plugins) config.plugins = {};
  const pluginsAllow = Array.isArray(config.plugins.allow) ? config.plugins.allow : [];
  if (!pluginsAllow.includes(PLUGIN_ID)) {
    config.plugins.allow = [...pluginsAllow, PLUGIN_ID];
    console.log(`[ClawMeeting] ✅ Added "${PLUGIN_ID}" to plugins.allow`);
    changed = true;
  }

  // ── 2. gateway.tools.allow — add required, remove stale plugin tool names ──
  if (!config.gateway) config.gateway = {};
  if (!config.gateway.tools) config.gateway.tools = {};
  if (!Array.isArray(config.gateway.tools.allow)) config.gateway.tools.allow = [];

  let gatewayAllow = config.gateway.tools.allow;

  // Remove plugin tool names (incorrectly added by older postinstall versions)
  const staleGateway = gatewayAllow.filter(t => PLUGIN_TOOLS.includes(t));
  if (staleGateway.length > 0) {
    gatewayAllow = gatewayAllow.filter(t => !PLUGIN_TOOLS.includes(t));
    console.log(`[ClawMeeting] 🧹 Removed stale plugin tools from gateway.tools.allow: [${staleGateway.join(", ")}]`);
    changed = true;
  }

  // Add required system tools if missing
  const missingGateway = REQUIRED_GATEWAY_TOOLS.filter(t => !gatewayAllow.includes(t));
  if (missingGateway.length > 0) {
    gatewayAllow = [...gatewayAllow, ...missingGateway];
    console.log(`[ClawMeeting] ✅ Added [${missingGateway.join(", ")}] to gateway.tools.allow`);
    changed = true;
  }

  config.gateway.tools.allow = gatewayAllow;

  // ── 3. tools.allow — remove stale plugin tool names ─────────────────────────
  if (config.tools && Array.isArray(config.tools.allow)) {
    const staleTools = config.tools.allow.filter(t => PLUGIN_TOOLS.includes(t));
    if (staleTools.length > 0) {
      config.tools.allow = config.tools.allow.filter(t => !PLUGIN_TOOLS.includes(t));
      console.log(`[ClawMeeting] 🧹 Removed stale plugin tools from tools.allow: [${staleTools.join(", ")}]`);
      changed = true;
    }
  }

  if (!changed) {
    console.log("[ClawMeeting] All config entries already correct ✅");
    process.exit(0);
  }

  writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");
  console.log("[ClawMeeting] 📝 openclaw.json updated.");
  console.log("=".repeat(60));
  console.log("[ClawMeeting] ⚠️  Please restart your OpenClaw gateway:");
  console.log("[ClawMeeting]     openclaw gateway restart");
  console.log("=".repeat(60));
} catch (err) {
  console.warn("[ClawMeeting] ⚠️ Failed to auto-configure:", err.message);
  console.warn(`[ClawMeeting] Please manually ensure:`);
  console.warn(`  - plugins.allow includes "${PLUGIN_ID}"`);
  console.warn(`  - gateway.tools.allow includes [${REQUIRED_GATEWAY_TOOLS.join(", ")}]`);
  console.warn(`  - gateway.tools.allow does NOT include plugin tools: [${PLUGIN_TOOLS.join(", ")}]`);
}
