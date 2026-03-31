#!/usr/bin/env node
// ClawMeeting postinstall script
// Ensures all required config entries exist in openclaw.json:
// 1. plugins.allow — plugin must be trusted for tools to be exposed to agent
// 2. gateway.tools.allow — sessions_send + message must be whitelisted for push

import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const PLUGIN_ID = "clawmeeting";
const REQUIRED_TOOLS = ["sessions_send", "message"];
const configPath = join(homedir(), ".openclaw", "openclaw.json");

if (!existsSync(configPath)) {
  console.log("[ClawMeeting] openclaw.json not found, skipping auto-config.");
  process.exit(0);
}

try {
  const raw = readFileSync(configPath, "utf-8");
  const config = JSON.parse(raw);
  let changed = false;

  // 1. plugins.allow
  if (!config.plugins) config.plugins = {};
  const pluginsAllow = Array.isArray(config.plugins.allow) ? config.plugins.allow : [];
  if (!pluginsAllow.includes(PLUGIN_ID)) {
    config.plugins.allow = [...pluginsAllow, PLUGIN_ID];
    console.log(`[ClawMeeting] ✅ Added "${PLUGIN_ID}" to plugins.allow`);
    changed = true;
  }

  // 2. gateway.tools.allow
  if (!config.gateway) config.gateway = {};
  if (!config.gateway.tools) config.gateway.tools = {};
  const toolsAllow = Array.isArray(config.gateway.tools.allow) ? config.gateway.tools.allow : [];
  const missing = REQUIRED_TOOLS.filter(t => !toolsAllow.includes(t));
  if (missing.length > 0) {
    config.gateway.tools.allow = [...toolsAllow, ...missing];
    console.log(`[ClawMeeting] ✅ Added [${missing.join(", ")}] to gateway.tools.allow`);
    changed = true;
  }

  if (!changed) {
    console.log("[ClawMeeting] All config entries already present ✅");
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
  console.warn(`  - gateway.tools.allow includes [${REQUIRED_TOOLS.join(", ")}]`);
}
