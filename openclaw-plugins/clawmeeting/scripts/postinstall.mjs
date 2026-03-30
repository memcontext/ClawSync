#!/usr/bin/env node
// ClawMeeting postinstall script
// Ensures required tools are in gateway.tools.allow in openclaw.json

import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const REQUIRED_TOOLS = ["sessions_send", "message"];
const configPath = join(homedir(), ".openclaw", "openclaw.json");

if (!existsSync(configPath)) {
  console.log("[ClawMeeting] openclaw.json not found, skipping auto-config.");
  process.exit(0);
}

try {
  const raw = readFileSync(configPath, "utf-8");
  const config = JSON.parse(raw);

  // Navigate to gateway.tools.allow
  if (!config.gateway) config.gateway = {};
  if (!config.gateway.tools) config.gateway.tools = {};
  if (!Array.isArray(config.gateway.tools.allow)) config.gateway.tools.allow = [];

  const allow = config.gateway.tools.allow;
  const missing = REQUIRED_TOOLS.filter(t => !allow.includes(t));

  if (missing.length === 0) {
    console.log(`[ClawMeeting] gateway.tools.allow already contains [${REQUIRED_TOOLS.join(", ")}] ✅`);
    process.exit(0);
  }

  config.gateway.tools.allow = [...allow, ...missing];
  writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");
  console.log(`[ClawMeeting] ✅ Added [${missing.join(", ")}] to gateway.tools.allow`);
  console.log("[ClawMeeting] Please restart your OpenClaw gateway for the change to take effect.");
} catch (err) {
  console.warn("[ClawMeeting] ⚠️ Failed to auto-configure tools:", err.message);
  console.warn(`[ClawMeeting] Please manually add [${REQUIRED_TOOLS.join(", ")}] to gateway.tools.allow in openclaw.json`);
}
