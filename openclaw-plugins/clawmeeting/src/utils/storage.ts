// ============================================================
// ClawMeeting Plugin - 本地存储工具
// 管理 Token、用户偏好等的本地持久化
// ============================================================

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import type { StoredCredentials, UserPreferences, SessionContext } from "../types/index.js";

// 存储路径（由 initStorage 初始化，基于插件 ID 动态生成）
let STORAGE_DIR = join(homedir(), ".openclaw", "clawmeeting");
let CREDENTIALS_FILE = join(STORAGE_DIR, "credentials.json");
let PREFERENCES_FILE = join(STORAGE_DIR, "preferences.json");
let SESSION_FILE = join(STORAGE_DIR, "session.json");

/** 初始化存储路径（插件加载时调用，传入插件 ID） */
export function initStorage(pluginId: string) {
  STORAGE_DIR = join(homedir(), ".openclaw", pluginId);
  CREDENTIALS_FILE = join(STORAGE_DIR, "credentials.json");
  PREFERENCES_FILE = join(STORAGE_DIR, "preferences.json");
  SESSION_FILE = join(STORAGE_DIR, "session.json");
}

/** 确保存储目录存在 */
function ensureDir() {
  if (!existsSync(STORAGE_DIR)) {
    mkdirSync(STORAGE_DIR, { recursive: true });
  }
}

// ---- 凭证管理 ----

export function saveCredentials(creds: StoredCredentials): void {
  ensureDir();
  writeFileSync(CREDENTIALS_FILE, JSON.stringify(creds, null, 2), "utf-8");
}

export function loadCredentials(): StoredCredentials | null {
  if (!existsSync(CREDENTIALS_FILE)) return null;
  try {
    const raw = readFileSync(CREDENTIALS_FILE, "utf-8");
    return JSON.parse(raw) as StoredCredentials;
  } catch {
    return null;
  }
}

export function clearCredentials(): void {
  if (existsSync(CREDENTIALS_FILE)) {
    writeFileSync(CREDENTIALS_FILE, "{}", "utf-8");
  }
}

// ---- 用户偏好/长期记忆 ----

export function savePreferences(prefs: UserPreferences): void {
  ensureDir();
  writeFileSync(PREFERENCES_FILE, JSON.stringify(prefs, null, 2), "utf-8");
}

export function loadPreferences(): UserPreferences {
  if (!existsSync(PREFERENCES_FILE)) {
    return {};
  }
  try {
    const raw = readFileSync(PREFERENCES_FILE, "utf-8");
    return JSON.parse(raw) as UserPreferences;
  } catch {
    return {};
  }
}

// ---- 已通知会议记录（去重用） ----

const NOTIFIED_FILE_NAME = "notified-meetings.json";

export function saveNotifiedMeetings(meetingIds: string[]): void {
  ensureDir();
  const filePath = join(STORAGE_DIR, NOTIFIED_FILE_NAME);
  writeFileSync(filePath, JSON.stringify(meetingIds), "utf-8");
}

export function loadNotifiedMeetings(): string[] {
  const filePath = join(STORAGE_DIR, NOTIFIED_FILE_NAME);
  if (!existsSync(filePath)) return [];
  try {
    const raw = readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as string[];
  } catch {
    return [];
  }
}



// ---- 等待用户决策的会议（COUNTER_PROPOSAL 通知后等用户回复）----

const PENDING_DECISIONS_FILE_NAME = "pending-decisions.json";

export function savePendingDecisions(meetingIds: string[]): void {
  ensureDir();
  const filePath = join(STORAGE_DIR, PENDING_DECISIONS_FILE_NAME);
  writeFileSync(filePath, JSON.stringify(meetingIds), "utf-8");
}

export function loadPendingDecisions(): string[] {
  const filePath = join(STORAGE_DIR, PENDING_DECISIONS_FILE_NAME);
  if (!existsSync(filePath)) return [];
  try {
    const raw = readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as string[];
  } catch {
    return [];
  }
}

// ---- Session 上下文 ----
// 记录用户绑定时的 session，确保轮询推送回到同一个对话窗口

export function saveSession(session: SessionContext): void {
  ensureDir();
  writeFileSync(SESSION_FILE, JSON.stringify(session, null, 2), "utf-8");
}

export function loadSession(): SessionContext | null {
  if (!existsSync(SESSION_FILE)) return null;
  try {
    const raw = readFileSync(SESSION_FILE, "utf-8");
    return JSON.parse(raw) as SessionContext;
  } catch {
    return null;
  }
}

// ---- Telegram Session 上下文 ----
// 单独持久化 Telegram 渠道的 session，叠加推送用

export function saveTelegramCtx(ctx: SessionContext): void {
  ensureDir();
  const filePath = join(STORAGE_DIR, "telegram-ctx.json");
  writeFileSync(filePath, JSON.stringify(ctx, null, 2), "utf-8");
}

export function loadTelegramCtx(): SessionContext | null {
  const filePath = join(STORAGE_DIR, "telegram-ctx.json");
  if (!existsSync(filePath)) return null;
  try {
    const raw = readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as SessionContext;
  } catch {
    return null;
  }
}
