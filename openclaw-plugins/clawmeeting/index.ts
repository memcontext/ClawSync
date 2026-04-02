// ============================================================
// ClawMeeting Plugin - 入口文件
// 架构设计：
//   1. 插件加载时：恢复 Token → 有 Token 则立即启动轮询 + 队列处理器
//   2. 轮询（10s）：发现新任务 → 去重 → 入队（collectTasks，毫秒级）
//   3. 队列处理器（5s）：逐条取出 → sessions_send → 提取 reply → message tool 分发
//   4. 失败重试：最多 3 次，超过则 fallback 到 prependContext + directMsg
//   5. Agent Offline：入队超 10 分钟未处理 → 自动 REJECT + 通知用户
//   6. 去重三层：notifiedMeetings(磁盘) / submittedMeetings(内存) / pendingDecisions(磁盘)
// ============================================================

import { readFileSync, writeFileSync, existsSync, unlinkSync, readdirSync, statSync } from "fs";
import { join, dirname } from "path";
import { homedir } from "os";
import { fileURLToPath } from "url";

// ESM 兼容：__dirname 在 "type":"module" 下不存在，需手动构造
const __filename_esm = fileURLToPath(import.meta.url);
const __dirname_esm = dirname(__filename_esm);
import { ClawMeetingApiClient } from "./src/utils/api-client.js";
import {
  initStorage,
  loadCredentials,
  saveSession,
  loadSession,
  loadNotifiedMeetings,
  saveNotifiedMeetings,
  loadPendingDecisions,
  savePendingDecisions,
  saveChannelCtx,
  loadAllChannelCtx,
} from "./src/utils/storage.js";
import { PollingManager } from "./src/utils/polling-manager.js";

// Tools
import {
  bindIdentitySchema,
  createBindIdentityHandler,
} from "./src/tools/bind-identity.js";
import {
  initiateMeetingSchema,
  createInitiateMeetingHandler,
} from "./src/tools/initiate-meeting.js";
import {
  verifyEmailCodeSchema,
  createVerifyEmailCodeHandler,
} from "./src/tools/verify-email-code.js";
import {
  checkAndRespondTasksSchema,
  createCheckAndRespondTasksHandler,
} from "./src/tools/check-and-respond-tasks.js";
import {
  listMeetingsSchema,
  createListMeetingsHandler,
} from "./src/tools/list-meetings.js";

// Types
import type { ClawMeetingPluginConfig, SessionContext, TaskType } from "./src/types/index.js";

// ---- 默认配置 ----
const DEFAULT_CONFIG: ClawMeetingPluginConfig = {
  serverUrl: "https://memcontext.ai/clawmeeting_api",
  pollingIntervalMs: 10000,
  autoRespond: true,
};


// ---- 从 manifest 读取插件 ID ----
function readPluginId(): string {
  try {
    const manifestPath = join(__dirname_esm, "openclaw.plugin.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    return manifest.id ?? "clawmeeting";
  } catch {
    return "clawmeeting";
  }
}

// ============================================================
// 配置自检（在 register 之前、模块加载时立即执行）
// ============================================================
// 框架 bug：当 plugins.allow 字段不存在时，ensurePluginAllowlisted 会跳过写入，
// 导致 openclaw plugin install 后工具不暴露给 agent。
// 所以我们在模块加载时（register 被调用前）就同步补全所有必要配置。

const PLUGIN_ID_FOR_ALLOW = "clawmeeting";
const REQUIRED_GATEWAY_TOOLS = ["sessions_send", "message"];
const RESTART_WELCOME_FLAG = join(homedir(), ".openclaw", "clawmeeting-restart-welcome.flag");


// 插件工具名 — 这些通过 api.registerTool() 注册，由框架自动暴露给 LLM，
// 绝不应该出现在 tools.allow 或 gateway.tools.allow 中（旧版本可能误写）
const PLUGIN_TOOL_NAMES = [
  "bind_identity",
  "verify_email_code",
  "initiate_meeting",
  "check_and_respond_tasks",
  "list_meetings",
];

/**
 * 补全 openclaw.json 中插件运行所需的全部配置。
 *
 * 时机：模块加载时立即执行（`openclaw plugins install` 期间）。
 * 此时 gateway 尚未启动 file watcher，写入不会触发热重载。
 * gateway 首次启动时会读取已更新的配置。
 *
 * 补全项：
 *   1. plugins.allow          — 插件信任列表（框架 bug 可能漏写）
 *   2. plugins.entries        — 插件启用状态
 *   3. gateway.tools.allow    — sessions_send / message 推送白名单
 */
function ensureAllConfig(): void {
  try {
    const configPath = join(homedir(), ".openclaw", "openclaw.json");
    if (!existsSync(configPath)) {
      console.log("[CM:config] openclaw.json 不存在，跳过自动配置");
      return;
    }
    const raw = readFileSync(configPath, "utf-8");
    const config = JSON.parse(raw);
    let changed = false;

    // ---- 1. plugins 根节点 ----
    if (!config.plugins) {
      config.plugins = {};
      changed = true;
    }

    // ---- 2. plugins.allow：必须是数组且包含 clawmeeting ----
    if (!Array.isArray(config.plugins.allow)) {
      config.plugins.allow = [PLUGIN_ID_FOR_ALLOW];
      console.log(`[CM:config] ✅ 创建 plugins.allow 并加入 "${PLUGIN_ID_FOR_ALLOW}"`);
      changed = true;
    } else if (!config.plugins.allow.includes(PLUGIN_ID_FOR_ALLOW)) {
      config.plugins.allow.push(PLUGIN_ID_FOR_ALLOW);
      console.log(`[CM:config] ✅ 已将 "${PLUGIN_ID_FOR_ALLOW}" 加入 plugins.allow`);
      changed = true;
    }

    // ---- 3. plugins.entries.clawmeeting.enabled ----
    if (!config.plugins.entries) config.plugins.entries = {};
    if (!config.plugins.entries[PLUGIN_ID_FOR_ALLOW]) {
      config.plugins.entries[PLUGIN_ID_FOR_ALLOW] = { enabled: true };
      console.log(`[CM:config] ✅ 创建 plugins.entries.${PLUGIN_ID_FOR_ALLOW} = { enabled: true }`);
      changed = true;
    } else if (config.plugins.entries[PLUGIN_ID_FOR_ALLOW].enabled === false) {
      config.plugins.entries[PLUGIN_ID_FOR_ALLOW].enabled = true;
      console.log(`[CM:config] ✅ 已将 plugins.entries.${PLUGIN_ID_FOR_ALLOW}.enabled 设为 true`);
      changed = true;
    }

    // ---- 4. gateway.tools.allow：添加 sessions_send + message ----
    if (!config.gateway) config.gateway = {};
    if (!config.gateway.tools) config.gateway.tools = {};
    if (!Array.isArray(config.gateway.tools.allow)) config.gateway.tools.allow = [];
    const missingGw = REQUIRED_GATEWAY_TOOLS.filter((t: string) => !config.gateway.tools.allow.includes(t));
    if (missingGw.length > 0) {
      config.gateway.tools.allow = [...config.gateway.tools.allow, ...missingGw];
      console.log(`[CM:config] ✅ 已将 [${missingGw.join(", ")}] 加入 gateway.tools.allow`);
      changed = true;
      // 写 flag：gateway 重启后 B-section 检测到 → 注入欢迎消息
      try {
        writeFileSync(RESTART_WELCOME_FLAG, "", "utf-8");
        console.log("[CM:config] 📌 已写入 restart-welcome flag");
      } catch { /* ignore */ }
    }

    // ---- 5. 清理：从 gateway.tools.allow 移除不该存在的插件工具名 ----
    const staleGw = config.gateway.tools.allow.filter((t: string) => PLUGIN_TOOL_NAMES.includes(t));
    if (staleGw.length > 0) {
      config.gateway.tools.allow = config.gateway.tools.allow.filter((t: string) => !PLUGIN_TOOL_NAMES.includes(t));
      console.log(`[CM:config] 🧹 从 gateway.tools.allow 移除误写的插件工具: [${staleGw.join(", ")}]`);
      changed = true;
    }

    // ---- 6. 清理：从 tools.allow 移除不该存在的插件工具名 ----
    if (config.tools && Array.isArray(config.tools.allow)) {
      const staleTools = config.tools.allow.filter((t: string) => PLUGIN_TOOL_NAMES.includes(t));
      if (staleTools.length > 0) {
        config.tools.allow = config.tools.allow.filter((t: string) => !PLUGIN_TOOL_NAMES.includes(t));
        if (config.tools.allow.length === 0) {
          delete config.tools.allow;
          console.log(`[CM:config] 🧹 tools.allow 清空后已删除（避免空白名单阻止所有工具）`);
        } else {
          console.log(`[CM:config] 🧹 从 tools.allow 移除误写的插件工具: [${staleTools.join(", ")}]`);
        }
        changed = true;
      }
    }

    // ---- 汇总 ----
    if (!changed) {
      console.log(`[CM:config] 全部配置已完整 ✅`);
      return;
    }

    writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");
    console.log("[CM:config] 📝 openclaw.json 已更新");
  } catch (err) {
    console.warn(`[CM:config] ⚠️ 自动配置失败: ${(err as Error)?.message}`);
  }
}

// 🔥 模块加载时立即执行 — 补全全部配置（install 期间，gateway file watcher 尚未激活）
ensureAllConfig();

// ---- 模块级共享上下文（跨多次 register() 调用，第一次初始化后永久有效）----
// OpenClaw 会为不同 Registry 多次调用 register()，工具必须每次都注册，
// 但运行时状态（API Client / 轮询 / 钩子）只初始化一次。
const _shared: {
  initialized: boolean;
  apiClient: ClawMeetingApiClient | null;
  pollingManager: PollingManager | null;
  pendingDecisions: Set<string> | null;
  submittedMeetings: Set<string> | null;
  refreshCredentials: (() => void) | null;
  startQueueProcessor: (() => void) | null;
} = {
  initialized: false,
  apiClient: null,
  pollingManager: null,
  pendingDecisions: null,
  submittedMeetings: null,
  refreshCredentials: null,
  startQueueProcessor: null,
};

export default function register(api: any) {
  // ============================================================
  // A. 工具注册（每次 register() 都执行，确保所有 Registry 都包含插件工具）
  //    工具 execute 闭包通过 _shared 引用运行时单例（第一次 register() 初始化）
  // ============================================================
  api.registerTool({
    ...bindIdentitySchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> bind_identity 调用: email=${params.email}`);
      const startMs = Date.now();
      const handler = createBindIdentityHandler(_shared.apiClient!, () => {
        _shared.refreshCredentials!();
        if (!_shared.pollingManager!.isRunning()) {
          console.log(`[CM:tool] bind_identity → 绑定成功，启动轮询`);
          _shared.pollingManager!.start();
          _shared.startQueueProcessor!();
        }
      });
      const result = await handler(params);
      console.log(`[CM:tool] <<< bind_identity 完成 (${Date.now() - startMs}ms): success=${(result as any).success}, already_bound=${(result as any).already_bound}`);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...verifyEmailCodeSchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> verify_email_code 调用: email=${params.email}, code=${params.code}`);
      const startMs = Date.now();
      const handler = createVerifyEmailCodeHandler(_shared.apiClient!, () => {
        _shared.refreshCredentials!();
        if (!_shared.pollingManager!.isRunning()) {
          console.log(`[CM:tool] verify_email_code → 绑定成功，启动轮询`);
          _shared.pollingManager!.start();
          _shared.startQueueProcessor!();
        }
      });
      const result = await handler(params);
      console.log(`[CM:tool] <<< verify_email_code 完成 (${Date.now() - startMs}ms): success=${(result as any).success}`);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...initiateMeetingSchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> initiate_meeting 调用: title="${params.title}", duration=${params.duration_minutes}min, invitees=[${params.invitees?.join(",")}], slots=${params.available_slots?.length ?? 0}个`);
      const startMs = Date.now();
      const handler = createInitiateMeetingHandler(_shared.apiClient!);
      const result = await handler(params);
      console.log(`[CM:tool] <<< initiate_meeting 完成 (${Date.now() - startMs}ms): success=${(result as any).success}, meeting_id=${(result as any).meeting_id ?? "N/A"}`);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...checkAndRespondTasksSchema,
    async execute(_id: string, params: any) {
      const isQuery = !params.meeting_id;
      if (isQuery) {
        console.log(`[CM:tool] >>> check_and_respond_tasks 调用 (Mode A: 查询任务列表)`);
      } else {
        console.log(`[CM:tool] >>> check_and_respond_tasks 调用 (Mode B: 提交响应) meeting_id=${params.meeting_id?.slice(-8)}, response_type=${params.response_type}, slots=${params.available_slots?.length ?? 0}个`);
      }
      const startMs = Date.now();
      const checkHandler = createCheckAndRespondTasksHandler(_shared.apiClient!);
      const result = await checkHandler(params);
      const elapsed = Date.now() - startMs;

      if (isQuery) {
        console.log(`[CM:tool] <<< check_and_respond_tasks 查询完成 (${elapsed}ms): success=${(result as any).success}, pending_count=${(result as any).pending_count ?? 0}`);
        if ((result as any).task_results?.length) {
          const tasks = (result as any).task_results;
          console.log(`[CM:tool]   任务详情: ${tasks.map((t: any) => `${t.task_type}(${t.meeting_id?.slice(-8)})`).join(", ")}`);
        }
      } else {
        console.log(`[CM:tool] <<< check_and_respond_tasks 提交完成 (${elapsed}ms): success=${(result as any).success}, status=${(result as any).status ?? "N/A"}`);
      }

      // 用户通过 Agent 提交了决策 → 清除等待状态，允许后续新轮次
      if (params.meeting_id && params.response_type && (result as any).success) {
        if (_shared.pendingDecisions!.has(params.meeting_id)) {
          _shared.pendingDecisions!.delete(params.meeting_id);
          savePendingDecisions([..._shared.pendingDecisions!]);
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} 用户已决策(${params.response_type})，从 pendingDecisions 移除`);
        }
        if (params.response_type !== "INITIAL") {
          _shared.submittedMeetings!.delete(params.meeting_id);
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} 从 submittedMeetings 移除（非 INITIAL）`);
        } else {
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} INITIAL 提交成功，保留在 submittedMeetings 中`);
        }
      }

      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...listMeetingsSchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> list_meetings 调用: meeting_id=${params.meeting_id ?? "(列表模式)"}`);
      const startMs = Date.now();
      const handler = createListMeetingsHandler(_shared.apiClient!);
      const result = await handler(params);
      console.log(`[CM:tool] <<< list_meetings 完成 (${Date.now() - startMs}ms): success=${(result as any).success}, total=${(result as any).total ?? "N/A"}`);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });
  console.log("[CM:init] 5 个工具已注册到当前 registry");

  // ============================================================
  // B. 运行时初始化（只执行一次 — 避免重复创建 API Client / 轮询 / 钩子）
  // ============================================================
  if (_shared.initialized) {
    console.log("[CM:init] register() 再次调用 — 工具已注册到新 registry，跳过运行时初始化");
    return;
  }
  _shared.initialized = true;

  const PKG_VERSION = JSON.parse(readFileSync(join(__dirname_esm, "package.json"), "utf-8")).version;
  console.log(`\n🐾🐾🐾 [ClawMeeting] v${PKG_VERSION} loaded 🐾🐾🐾\n`);

  // 双保险：如果模块顶层执行时 openclaw.json 还没就绪
  ensureAllConfig();

  const PLUGIN_ID = readPluginId();

  // ============================================================
  // 1. 读取插件配置
  // ============================================================
  const pluginConfig: ClawMeetingPluginConfig = {
    ...DEFAULT_CONFIG,
    ...(api.config?.plugins?.entries?.[PLUGIN_ID]?.config ?? {}),
  };
  console.log(`[CM:init] 插件配置: serverUrl=${pluginConfig.serverUrl}, pollingInterval=${pluginConfig.pollingIntervalMs}ms, autoRespond=${pluginConfig.autoRespond}`);

  // 初始化存储目录
  initStorage(PLUGIN_ID);

  // ============================================================
  // 2. 初始化 API Client + 恢复 Token
  // ============================================================
  const apiClient = new ClawMeetingApiClient(pluginConfig.serverUrl);

  let savedCreds = loadCredentials();
  if (savedCreds?.token) {
    apiClient.setToken(savedCreds.token);
    console.log(`[CM:init] 已恢复身份凭证: email=${savedCreds.email}, user_id=${savedCreds.user_id}, token=${savedCreds.token?.substring(0, 12)}...`);
  } else {
    console.log(`[CM:init] 无已保存的身份凭证`);
  }

  /** 绑定成功后刷新内存中的 savedCreds 引用（修复 mid-session 绑定后 system prompt 显示 unknown） */
  function refreshCredentials() {
    savedCreds = loadCredentials();
  }

  // ============================================================
  // 3. Session 管理（主 session + 额外渠道 session）
  // ============================================================
  const WEBCHAT_CHANNELS = new Set(["webchat", "web", "main"]);

  // 主 session（webchat）— 校验：sessionKey 不能是渠道 session（防止历史脏数据）
  let sessionCtx: SessionContext = loadSession() ?? { sessionKey: "agent:main:main" };
  if (sessionCtx.sessionKey.split(":").length >= 5 && !WEBCHAT_CHANNELS.has(sessionCtx.sessionKey.split(":")[2])) {
    console.log(`[CM:init] 主 session 脏数据检测: ${sessionCtx.sessionKey} 是渠道 session，重置为 agent:main:main`);
    sessionCtx = { sessionKey: "agent:main:main", channel: "webchat" };
    saveSession(sessionCtx);
  }
  console.log(`[CM:init] 主 session: key=${sessionCtx.sessionKey}, channel=${sessionCtx.channel ?? "未知"}`);

  // 额外渠道 session（Telegram/飞书/Discord 等，通用 Map，新渠道零改动）
  const extraChannels: Map<string, SessionContext> = loadAllChannelCtx();
  if (extraChannels.size > 0) {
    for (const [ch, ctx] of extraChannels) {
      console.log(`[CM:init] 渠道 ${ch} session (从磁盘恢复): key=${ctx.sessionKey}`);
    }
  }

  // 渠道自动发现：遍历 api.config.channels，从 pairing allow store 读取
  const configuredChannels = api.config?.channels ?? {};
  for (const [channelName, channelCfg] of Object.entries(configuredChannels)) {
    if (!(channelCfg as any)?.enabled) continue;
    if (WEBCHAT_CHANNELS.has(channelName)) continue;
    if (extraChannels.has(channelName)) continue; // 已从磁盘恢复

    try {
      const allowStorePath = join(homedir(), ".openclaw", "credentials", `${channelName}-default-allowFrom.json`);
      if (existsSync(allowStorePath)) {
        const storeData = JSON.parse(readFileSync(allowStorePath, "utf-8"));
        const allowFrom: string[] = storeData?.allowFrom ?? [];
        if (allowFrom.length > 0) {
          const ctx: SessionContext = {
            sessionKey: `agent:main:${channelName}:direct:${allowFrom[0]}`,
            channel: channelName,
          };
          extraChannels.set(channelName, ctx);
          saveChannelCtx(channelName, ctx);
          console.log(`[CM:init] ${channelName} 自动发现: target=${allowFrom[0]}, sessionKey=${ctx.sessionKey}`);
        }
      }
    } catch (err) {
      console.log(`[CM:init] 读取 ${channelName} allow store 失败: ${err}`);
    }
  }

  if (extraChannels.size === 0) {
    console.log(`[CM:init] 无额外渠道（Telegram/飞书等未配置或未配对）`);
  } else {
    console.log(`[CM:init] 已发现 ${extraChannels.size} 个额外渠道: [${[...extraChannels.keys()].join(", ")}]`);
  }

  // ============================================================
  // 4. Gateway 认证 Token（用于主动推送消息）
  // ============================================================
  const gatewayPort = api.config?.gateway?.port ?? 18789;
  const gatewayToken = api.config?.gateway?.auth?.token ?? null;

  if (gatewayToken) {
    console.log(`[CM:init] gateway: port=${gatewayPort}, token=${gatewayToken.substring(0, 12)}... → 主动推送可用`);
  } else {
    console.log(`[CM:init] gateway: port=${gatewayPort}, token=NULL → 主动推送不可用，通知将在用户下次交互时展示`);
  }

  // ============================================================
  // 5. 主动推送（sessions_send 到所有 session，agent 在每个 session 独立处理）
  // ============================================================

  /**
   * 从 session transcript 文件中提取最近的 assistant 完整回复
   * 用于 sessions_send 返回 error/timeout 后补捞 agent 的实际回复
   */
  /**
   * 从 session transcript 中精确提取某个任务的 agent 回复。
   *
   * 不依赖 sessions_list（gateway restart 后返回的 transcript 路径可能过期），
   * 直接扫描 sessions 目录中最近修改的 .jsonl 文件，用 [ClawMeeting {taskType}] + meeting_id 定位锚点，
   * 沿锚点往后找属于该任务的最终 assistant 回复。
   */
  async function pollReplyFromTranscript(
    sessionKey: string, meetingId: string, taskType: string, afterTs: number,
    maxWaitMs: number = 40000, pollIntervalMs: number = 5000,
  ): Promise<string | undefined> {
    const sessionsDir = join(homedir(), ".openclaw", "agents", "main", "sessions");
    const anchorMarker = `ClawMeeting ${taskType}`;

    console.log(`[CM:poll-reply] 扫描 ${sessionsDir} meetingId=${meetingId?.slice(-8)} taskType=${taskType} (最长 ${maxWaitMs / 1000}s)`);
    const startMs = Date.now();

    while (Date.now() - startMs < maxWaitMs) {
      await new Promise(r => setTimeout(r, pollIntervalMs));

      try {
        // 1. 找最近修改的 .jsonl 文件（最多 3 个，按 mtime 倒序）
        let files: { name: string; mtime: number }[];
        try {
          files = readdirSync(sessionsDir)
            .filter(f => f.endsWith(".jsonl"))
            .map(f => ({ name: f, mtime: statSync(join(sessionsDir, f)).mtimeMs }))
            .sort((a, b) => b.mtime - a.mtime)
            .slice(0, 3);
        } catch {
          console.warn(`[CM:poll-reply] 无法读取 sessions 目录`);
          continue;
        }

        // 2. 在每个文件中搜索锚点 + 提取回复
        for (const file of files) {
          const filePath = join(sessionsDir, file.name);
          const content = readFileSync(filePath, "utf-8");
          const lines = content.trim().split("\n");

          // 2a. 从后往前找锚点
          let anchorIdx = -1;
          for (let i = lines.length - 1; i >= Math.max(0, lines.length - 50); i--) {
            try {
              if (!lines[i].includes(anchorMarker) || !lines[i].includes(meetingId)) continue;
              const entry = JSON.parse(lines[i]);
              if (entry?.message?.role === "user") { anchorIdx = i; break; }
            } catch {}
          }

          if (anchorIdx < 0) continue; // 此文件没有锚点，试下一个

          // 2b. 从锚点往后找 assistant 回复（遇到下一个任务 break）
          let lastReply: string | undefined;
          for (let i = anchorIdx + 1; i < Math.min(anchorIdx + 30, lines.length); i++) {
            try {
              const entry = JSON.parse(lines[i]);
              const msg = entry?.message;

              // 任务边界：下一个 [ClawMeeting 或 [System 开头的 user 消息
              if (msg?.role === "user") {
                const txt = Array.isArray(msg.content) ? msg.content.map((c: any) => c.text ?? "").join("") : "";
                if (txt.includes("[ClawMeeting") || txt.includes("[System")) break;
              }

              if (msg?.role === "assistant" && msg?.content) {
                const texts = Array.isArray(msg.content)
                  ? msg.content.filter((c: any) => c.type === "text" && c.text).map((c: any) => c.text)
                  : [typeof msg.content === "string" ? msg.content : ""];
                const combined = texts.join("\n").trim();
                if (combined.length > 50) lastReply = combined;
              }
            } catch {}
          }

          if (lastReply) {
            console.log(`[CM:poll-reply] 补捞成功 file=${file.name.slice(0, 8)} (${lastReply.length}字, ${Date.now() - startMs}ms)`);
            return lastReply;
          }
          console.log(`[CM:poll-reply] file=${file.name.slice(0, 8)} 锚点已找到但链上无有效回复`);
        }

        console.log(`[CM:poll-reply] 轮询中 (${Math.round((Date.now() - startMs) / 1000)}s/${maxWaitMs / 1000}s) 已扫 ${files.length} 个文件`);
      } catch (e) {
        console.warn(`[CM:poll-reply] 扫描异常: ${(e as Error)?.message}`);
      }
    }

    console.log(`[CM:poll-reply] 轮询超时 meetingId=${meetingId?.slice(-8)} taskType=${taskType}`);
    return undefined;
  }

  /**
   * 通过 sessions_send 发送到指定 session，触发 agent turn
   */
  async function sendViaSessionsSend(message: string, sessionKey?: string): Promise<{ ok: boolean; reply?: string; timedOut?: boolean; agentTriggered?: boolean }> {
    const sk = sessionKey ?? sessionCtx.sessionKey ?? "agent:main:main";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000); // 60s：agent 可能调用 LLM 或等待用户输入
    const startMs = Date.now();

    console.log(`[CM:push] >>> sessions_send 目标=${sk} 消息长度=${message.length} 消息前100字="${message.substring(0, 100).replace(/\n/g, "\\n")}"`);

    try {
      const res = await fetch(`http://127.0.0.1:${gatewayPort}/tools/invoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${gatewayToken}`,
        },
        body: JSON.stringify({
          tool: "sessions_send",
          args: {
            sessionKey: sk,
            role: "system",
            message,
            delivery: { mode: "none" },
            announce: false,
          },
        }),
        signal: controller.signal,
      });

      clearTimeout(timeout);
      const elapsed = Date.now() - startMs;

      if (res.ok) {
        const body = await res.text();
        // 关键：HTTP 200 不代表成功，需要解析 body 检测 forbidden/error
        const isForbidden = body.includes('"status":"forbidden"') || body.includes('"status": "forbidden"');
        if (isForbidden) {
          console.error(`[CM:push] <<< sessions_send forbidden → ${sk} (${elapsed}ms) body=${body.substring(0, 400)}`);
          // gateway 白名单可能缺少 sessions_send → 补写配置（需重启 gateway 生效）
          ensureAllConfig();
          console.warn(
            "[CM:push] 💡 部分推送功能尚未激活，重启网关即可体验完整功能：openclaw gateway restart"
          );
          return { ok: false };
        }

        // 检测 status:"error" — gateway WS 连接异常关闭，agent 可能仍在执行但 reply 未捕获
        const isError = body.includes('"status":"error"') || body.includes('"status": "error"');
        if (isError) {
          let errorReason = "unknown";
          try { errorReason = JSON.parse(body)?.result?.details?.error ?? "unknown"; } catch {}
          console.error(`[CM:push] <<< sessions_send error → ${sk} (${elapsed}ms): ${errorReason.substring(0, 200)}`);
          console.warn(`[CM:push] ⚠️ agent 已触发但 WS 断开，reply 无法捕获（可从 transcript 补捞）`);
          return { ok: false, agentTriggered: true };
        }

        // 提取 agent 完整回复
        let reply: string | undefined;
        let timedOut = false;
        try {
          const json = JSON.parse(body);
          const details = json?.result?.details;

          // 优先：从 messages 数组中提取所有 assistant 文本（完整回复）
          if (Array.isArray(details?.messages)) {
            const assistantTexts = details.messages
              .filter((m: any) => m.role === "assistant" && m.content)
              .map((m: any) => {
                if (typeof m.content === "string") return m.content;
                if (Array.isArray(m.content)) {
                  return m.content
                    .filter((c: any) => c.type === "text" && c.text)
                    .map((c: any) => c.text)
                    .join("\n");
                }
                return "";
              })
              .filter(Boolean);
            if (assistantTexts.length > 0) {
              reply = assistantTexts.join("\n\n");
              console.log(`[CM:push] reply 提取自 messages 数组 (${assistantTexts.length} 条 assistant 消息, ${reply.length} 字)`);
            }
          }

          // 其次：content 字段（可能是完整文本）
          if (!reply && details?.content) {
            reply = typeof details.content === "string"
              ? details.content
              : JSON.stringify(details.content);
            console.log(`[CM:push] reply 提取自 details.content (${reply.length} 字)`);
          }

          // 兜底：reply 字段（只有最后一段摘要）
          if (!reply && details?.reply) {
            reply = details.reply;
            console.log(`[CM:push] reply 提取自 details.reply (兜底, ${reply.length} 字)`);
          }

          // 检测 gateway 端 agent.wait 超时
          timedOut = details?.status === "timeout";
          if (timedOut) {
            console.warn(`[CM:push] ⚠️ sessions_send gateway 超时 (agent.wait 到期)，agent 可能已处理但 reply 未捕获`);
          }

          // debug：打印完整 body 结构 key
          const detailKeys = details ? Object.keys(details).join(",") : "null";
          console.log(`[CM:push] response details keys=[${detailKeys}]${timedOut ? " [TIMEOUT]" : ""} body前500字=${body.substring(0, 500)}`);
        } catch (_e) {
          console.warn(`[CM:push] response body 非 JSON: ${body.substring(0, 300)}`);
        }

        console.log(`[CM:push] <<< sessions_send ${timedOut ? "超时(gateway)" : "成功"} → ${sk} (${elapsed}ms) reply=${reply ? `"${reply.substring(0, 150)}..."` : "无"}`);
        return { ok: true, reply, timedOut };
      } else {
        const body = await res.text();
        console.error(`[CM:push] <<< sessions_send 失败 → ${sk} (${elapsed}ms) HTTP=${res.status} body=${body.substring(0, 300)}`);
        return { ok: false };
      }
    } catch (err) {
      clearTimeout(timeout);
      const elapsed = Date.now() - startMs;
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error(`[CM:push] <<< sessions_send 异常 → ${sk} (${elapsed}ms): ${errMsg}`);
      return { ok: false };
    }
  }

  /**
   * 从 sessionKey 解析渠道和目标 ID
   * 格式: "agent:main:telegram:direct:6866253526" → { channel: "telegram", target: "6866253526" }
   */
  function parseChannelTarget(sessionKey: string): { channel: string; target: string } | null {
    const parts = sessionKey.split(":");
    // agent:main:<channel>:<kind>:<id>
    if (parts.length >= 5 && parts[2] !== "main" && parts[2] !== "webchat" && parts[2] !== "web") {
      return { channel: parts[2], target: parts[4] };
    }
    return null;
  }

  /**
   * 通过 message tool 直接发消息到渠道（Telegram/Discord/Feishu 等）
   * 不触发 agent turn，用户直接看到消息内容
   */
  async function sendViaMessageTool(channel: string, target: string, message: string): Promise<{ ok: boolean }> {
    const startMs = Date.now();
    console.log(`[CM:push] >>> message tool 目标=${channel}:${target} 消息长度=${message.length} 摘要="${message.substring(0, 100).replace(/\n/g, "\\n")}"`);

    try {
      const res = await fetch(`http://127.0.0.1:${gatewayPort}/tools/invoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${gatewayToken}`,
        },
        body: JSON.stringify({
          tool: "message",
          args: {
            action: "send",
            channel,
            target,
            message,
          },
        }),
      });

      const elapsed = Date.now() - startMs;
      const body = await res.text();

      if (res.ok) {
        console.log(`[CM:push] <<< message tool 成功 → ${channel}:${target} (${elapsed}ms) body=${body.substring(0, 200)}`);
        return { ok: true };
      } else {
        console.error(`[CM:push] <<< message tool 失败 → ${channel}:${target} (${elapsed}ms) HTTP=${res.status} body=${body.substring(0, 300)}`);
        return { ok: false };
      }
    } catch (err) {
      const elapsed = Date.now() - startMs;
      console.error(`[CM:push] <<< message tool 异常 → ${channel}:${target} (${elapsed}ms): ${(err as Error)?.message}`);
      return { ok: false };
    }
  }

  /**
   * 统一推送到所有额外渠道（Telegram/飞书/Discord 等）
   * 所有渠道都失败时自动 fallback 到 pendingNotifications（webchat prependContext）
   */
  async function pushToExtraChannels(msg: string): Promise<boolean> {
    if (!msg.trim() || extraChannels.size === 0) return false;
    let anyOk = false;
    for (const [chName, chCtx] of extraChannels) {
      const target = parseChannelTarget(chCtx.sessionKey);
      if (!target) continue;
      const { ok } = await sendViaMessageTool(target.channel, target.target, msg);
      if (ok) anyOk = true;
      else console.warn(`[CM:push] ${chName} 渠道推送失败 (${target.channel}:${target.target})`);
    }
    if (!anyOk && extraChannels.size > 0) {
      pendingNotifications.push(msg);
      console.log(`[CM:push] 所有渠道推送失败 → 已加入 pendingNotifications (${msg.length}字)`);
    }
    return anyOk;
  }

  // ============================================================
  // 6. 通知去重 + 提交去重
  // ============================================================
  const notifiedMeetings = new Set<string>(loadNotifiedMeetings());
  // 记录已成功提交但仍在 COLLECTING 的会议，避免无限重试
  const submittedMeetings = new Set<string>();
  // 等待用户决策的会议（COUNTER_PROPOSAL 已通知用户，等回复）
  const pendingDecisions = new Set<string>(loadPendingDecisions());
  console.log(`[CM:dedup] 初始状态: notifiedMeetings=${notifiedMeetings.size}个 [${[...notifiedMeetings].map(id => id.slice(-8)).join(",")}]`);
  console.log(`[CM:dedup] 初始状态: submittedMeetings=0个 (内存，重启清空)`);
  console.log(`[CM:dedup] 初始状态: pendingDecisions=${pendingDecisions.size}个 [${[...pendingDecisions].map(id => id.slice(-8)).join(",")}]`);

  // ============================================================
  // 7. 任务队列 + 待推送通知（fallback）
  // ============================================================
  interface QueuedTask {
    task: any;              // 原始任务对象
    retryCount: number;     // sessions_send 重试次数
    enqueuedAt: number;     // Date.now() 入队时间
    agentMsg: string;       // buildAgentNotification 预构建（给 agent）
    directMsg: string;      // buildDirectNotification 预构建（给渠道/fallback）
  }
  const taskQueue: QueuedTask[] = [];
  let isProcessingQueue = false;
  const QUEUE_PROCESS_INTERVAL = 5000;  // 5s 处理一次
  const MAX_RETRY = 3;
  const OFFLINE_TIMEOUT_MS = 10 * 60 * 1000; // 10 分钟
  let pendingNotifications: string[] = [];

  // ============================================================
  // 8. 通知构建
  // ============================================================

  /** 给主 session agent 的通知（按 task_type 差异化指令，agent 处理后回复用户） */
  function buildAgentNotification(t: any): string {
    const msg = t.message ?? "";
    const taskType = t.task_type;
    const header = `[ClawMeeting ${taskType}]`;
    // 所有类型都附带 meeting_id，用于 transcript 补捞时精确定位锚点
    const idLine = t.meeting_id ? `Meeting ID: ${t.meeting_id}` : "";

    // 通用规则：用用户的语言回复，保留所有字段不省略
    const langRule = "Reply in the user's language (detect from conversation history). Do NOT omit any fields from the notification.";

    if (taskType === "MEETING_CONFIRMED") {
      return [header, idLine, msg, "",
        `Instruction: Present this confirmation to the user. Include ALL details: meeting title, confirmed time, duration, organizer, participants, and meeting link (if present). Format it clearly. ${langRule}`,
      ].filter(Boolean).join("\n");
    }
    if (taskType === "MEETING_OVER") {
      return [header, idLine, msg, "",
        `Instruction: Inform the user this meeting has been cancelled. Include the meeting title, reason (if provided), and who cancelled. ${langRule}`,
      ].filter(Boolean).join("\n");
    }
    if (taskType === "COUNTER_PROPOSAL") {
      return [header, idLine, msg, "",
        [
          "Instruction: The coordinator has sent a compromise proposal. This REQUIRES the user's decision.",
          "1. Present ALL details: meeting title, proposed time slots, coordinator's reasoning.",
          "2. Clearly ask the user to choose: Accept / Propose new times / Reject.",
          "3. Wait for the user's explicit decision before taking any action.",
          "4. When submitting, ALWAYS include preference_note with user's reasoning.",
          "5. CRITICAL: When the user replies with a decision (accept/reject/cancel/new times), you MUST call check_and_respond_tasks to execute it.",
          "   'cancel'/'reject'/'not attending' -> response_type=REJECT. 'accept'/'agree'/'works for me' -> response_type=ACCEPT_PROPOSAL.",
          "   Do NOT just acknowledge verbally — always call the tool.",
          langRule,
        ].join(" "),
      ].filter(Boolean).join("\n");
    }
    if (taskType === "MEETING_FAILED") {
      return [header, idLine, msg, "",
        [
          "Instruction: Meeting negotiation has failed. This REQUIRES the user's decision.",
          "1. Present the failure reason and meeting details.",
          "2. Clearly tell the user they have THREE options:",
          "   a) Cancel this meeting entirely.",
          "   b) Modify available time slots and retry.",
          "   c) Modify meeting setup (not just time preference), e.g. shorten duration, split into two meetings, add/remove participants, change format (online/offline/email).",
          "3. Wait for the user's explicit choice. Do NOT proceed without it.",
          "4. If user chooses option (c), collect the concrete change plan first, then execute with tools:",
          "   first call check_and_respond_tasks with response_type=REJECT to close this failed negotiation,",
          "   then call initiate_meeting to create the revised meeting.",
          "5. CRITICAL: When the user replies with a decision, you MUST call tools to execute it.",
          "   'cancel'/'stop'/'drop it'/'not proceeding' -> response_type=REJECT. Never just acknowledge verbally - always call the tool.",
          langRule,
        ].join(" "),
      ].filter(Boolean).join("\n");
    }
    if (taskType === "INITIAL_SUBMIT") {
      const lines = [header];
      lines.push(`Meeting: "${t.title ?? "unknown"}"`);
      lines.push(`Meeting ID: ${t.meeting_id}`);
      lines.push(`Organizer: ${t.initiator ?? "unknown"}`);
      lines.push(`Duration: ${t.duration_minutes ?? "unknown"} minutes`);
      if (msg) lines.push("", msg);
      // 补充已提交的参与者时段信息
      if (t._submittedParticipants?.length > 0) {
        lines.push("", "Submitted available slots:");
        for (const p of t._submittedParticipants) {
          lines.push(`  ${p.email} (${p.role}): ${p.latest_slots.join(", ")}`);
        }
      }
      lines.push("", [
        "Instruction: You received a meeting invitation. Handle it FAST (aim for <20s):",
        "",
        "Step 1: Check if calendar tool is available (feishu_calendar_event / google_calendar).",
        "  - YES → Query calendar for the proposed time range to get real conflicts. Then go to Step 3.",
        "  - NO (tool not available or auth error) → Go to Step 2.",
        "",
        "Step 2 (No calendar): Check memory/summary for known schedule info (business trips, recurring meetings, preferences).",
        "  - If you find clear conflicts or free slots → Go to Step 3.",
        "  - If insufficient info → Present the meeting details to the user and ask them to confirm which time slots work. Do NOT auto-submit. Skip to output format.",
        "",
        "Step 3: Execution — Based on calendar/memory data:",
        "  - Confident (clear free slots, no ambiguity) → call check_and_respond_tasks immediately (response_type=INITIAL, available_slots=free slots).",
        "  - Conflicts or ambiguous → Present analysis to user, ask for decision. Do NOT auto-submit.",
        "  - Clearly impossible (all slots conflict) → call check_and_respond_tasks(response_type=REJECT).",
        "",
        "IMPORTANT: Do NOT call multiple tools sequentially (calendar + memory + submit). Pick ONE path and execute. Speed matters.",
        "",
        "After processing, reply using this EXACT structured format (MANDATORY — never skip or simplify):",
        "",
        "### Meeting Basics",
        "- **Meeting Title**: [Meeting Title]",
        "- **Organizer**: [Organizer Email]",
        "- **Duration**: [Duration]",
        "",
        "### Schedule Check Results",
        "- **Data Source**: [Calendar Query / Memory Inference / No Available Data]",
        "- **Available Slots**: [List free slots, or 'Unknown (user confirmation required)']",
        "- **Conflict Notes**: [Specific conflicts, or 'No known conflicts']",
        "",
        "### Action Outcome",
        "- **Executed Action**: [e.g., 'Auto-submitted: 13:00-18:00 fully available' / 'Waiting for your confirmation before submit']",
        "- **Pending Confirmation**: [If needed: 'Which slots work for you?']",
        "",
        "NEVER reply with a single paragraph or just 'submitted/rejected'. Always provide the full structured report above.",
        langRule,
      ].join("\n"));
      return lines.join("\n");
    }
    // 其他类型：通用转告
    return [header, idLine, msg, "",
      `Instruction: Relay this notification to the user. Preserve all fields and details exactly. ${langRule}`,
    ].filter(Boolean).join("\n");
  }

  /** 给用户直接看的通知（不含 agent 指令，用于 message tool 直推到 Telegram 等渠道） */
  function buildDirectNotification(t: any): string {
    const title = t.title ?? "Unknown Meeting";
    const msg = t.message ?? "";
    const taskType = t.task_type;

    if (taskType === "MEETING_CONFIRMED") {
      const parts = [`✅ Meeting Confirmed: "${title}"`];
      if (t.final_time) parts.push(`⏰ Time: ${t.final_time}`);
      if (t.duration_minutes) parts.push(`⏱️ Duration: ${t.duration_minutes} minutes`);
      if (t.meeting_link) parts.push(`🔗 Link: ${t.meeting_link}`);
      if (t.initiator) parts.push(`👤 Organizer: ${t.initiator}`);
      if (t._participants) parts.push(`👥 Participants: ${t._participants}`);
      if (msg) parts.push("", msg);
      return parts.join("\n");
    }
    if (taskType === "MEETING_OVER") {
      const parts = [`❌ Meeting Cancelled: "${title}"`];
      if (t.initiator) parts.push(`👤 Organizer: ${t.initiator}`);
      if (msg) parts.push("", msg);
      return parts.join("\n");
    }
    if (taskType === "COUNTER_PROPOSAL") {
      const parts = [`🔄 Counter-Proposal: "${title}" — Your decision is needed`];
      if (t.initiator) parts.push(`👤 Organizer: ${t.initiator}`);
      if (t.duration_minutes) parts.push(`⏱️ Duration: ${t.duration_minutes} minutes`);
      if (msg) parts.push("", msg);
      parts.push("", "Please reply in the conversation with your decision (Accept / Propose new times / Reject).");
      return parts.join("\n");
    }
    if (taskType === "MEETING_FAILED") {
      const parts = [`❌ Meeting Negotiation Failed: "${title}"`];
      if (t.initiator) parts.push(`👤 Organizer: ${t.initiator}`);
      if (msg) parts.push("", msg);
      parts.push("", "Please reply in the conversation with your decision (Cancel / Adjust and retry / Change meeting setup).");
      return parts.join("\n");
    }
    if (taskType === "INITIAL_SUBMIT") {
      const parts = [`📅 Meeting Invitation: "${title}"`];
      if (t.initiator) parts.push(`👤 Organizer: ${t.initiator}`);
      if (t.duration_minutes) parts.push(`⏱️ Duration: ${t.duration_minutes} minutes`);
      if (msg) parts.push("", msg);
      return parts.join("\n");
    }
    const parts = [`📅 ${title}`];
    if (t.initiator) parts.push(`👤 ${t.initiator}`);
    if (msg) parts.push(msg);
    return parts.join("\n");
  }


  // ============================================================
  // 9. 任务收集 + 队列处理（逐条：sessions_send → 提取 reply → message tool 分发）
  // ============================================================

  /**
   * collectTasks: 去重过滤 + 构建通知文本 + 入队
   * 仅做入队，不做推送（毫秒级，不阻塞轮询）
   */
  async function collectTasks(tasks: unknown[]): Promise<string[]> {
    console.log(`[CM:collect] === 收集 ${tasks.length} 个任务 ===`);
    console.log(`[CM:collect] 去重状态: notified=${notifiedMeetings.size}, submitted=${submittedMeetings.size}, pending=${pendingDecisions.size}, queue=${taskQueue.length}`);

    for (const task of tasks) {
      const t = task as any;
      const meetingId = t.meeting_id;
      const title = t.title ?? "未知会议";
      const taskType = t.task_type;

      console.log(`[CM:collect] 任务: type=${taskType}, meetingId=${meetingId?.slice(-8)}, title="${title}"`);

      // ---- INITIAL_SUBMIT：能自动处理就自动，否则通知用户决策 ----
      if (taskType === "INITIAL_SUBMIT") {
        if (submittedMeetings.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 submittedMeetings`); continue; }
        if (notifiedMeetings.has(`${meetingId}:INITIAL_SUBMIT`)) { console.log(`[CM:collect]   → 跳过: 已在 notifiedMeetings (AGENT_OFFLINE 后)`); continue; }
        if (pendingDecisions.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 pendingDecisions`); continue; }
        // 检查是否已在队列中
        if (taskQueue.some(q => q.task.meeting_id === meetingId)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }

        submittedMeetings.add(meetingId);

        // 拉取详情补充发起人的已提交时段，丰富 task 对象供 buildAgentNotification 使用
        try {
          const detail = await apiClient.getMeetingDetail(meetingId);
          const submitted = detail.participants.filter(
            (p: any) => p.has_submitted && p.latest_slots?.length > 0,
          );
          if (submitted.length > 0) {
            t._submittedParticipants = submitted;
          }
        } catch (_e) { /* ignore */ }

        taskQueue.push({
          task: t,
          retryCount: 0,
          enqueuedAt: Date.now(),
          agentMsg: buildAgentNotification(t),
          directMsg: buildDirectNotification(t),
        });
        console.log(`[CM:collect]   → 入队 INITIAL_SUBMIT: 「${title}」(${meetingId})`);
        continue;
      }

      // ---- COUNTER_PROPOSAL：需要用户决策 ----
      if (taskType === "COUNTER_PROPOSAL") {
        if (pendingDecisions.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 pendingDecisions`); continue; }
        if (taskQueue.some(q => q.task.meeting_id === meetingId)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }
        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        taskQueue.push({
          task: t,
          retryCount: 0,
          enqueuedAt: Date.now(),
          agentMsg: buildAgentNotification(t),
          directMsg: buildDirectNotification(t),
        });
        console.log(`[CM:collect]   → 入队 COUNTER_PROPOSAL: 「${title}」(${meetingId})`);
        continue;
      }

      // ---- MEETING_FAILED：需要发起人决策 ----
      if (taskType === "MEETING_FAILED") {
        if (pendingDecisions.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 pendingDecisions`); continue; }
        if (taskQueue.some(q => q.task.meeting_id === meetingId)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }
        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        taskQueue.push({
          task: t,
          retryCount: 0,
          enqueuedAt: Date.now(),
          agentMsg: buildAgentNotification(t),
          directMsg: buildDirectNotification(t),
        });
        console.log(`[CM:collect]   → 入队 MEETING_FAILED: 「${title}」(${meetingId})`);
        continue;
      }

      // ---- CONFIRMED / OVER 等：纯通知（用 meetingId:taskType 去重，同一会议不同阶段独立通知）----
      const dedupKey = `${meetingId}:${taskType}`;
      if (notifiedMeetings.has(dedupKey)) { console.log(`[CM:collect]   → 跳过: 已在 notifiedMeetings (${dedupKey})`); continue; }
      if (taskQueue.some(q => q.task.meeting_id === meetingId && q.task.task_type === taskType)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }
      notifiedMeetings.add(dedupKey);

      // 拉取 meeting detail 补充关键字段（task 对象本身只有 title/message，缺少 final_time/duration/link）
      try {
        const detail = await apiClient.getMeetingDetail(meetingId);
        if (detail.final_time) t.final_time = detail.final_time;
        if (detail.duration_minutes) t.duration_minutes = detail.duration_minutes;
        if ((detail as any).meeting_link) t.meeting_link = (detail as any).meeting_link;
        if (detail.participants) {
          t._participants = detail.participants.map((p: any) => p.email).join(", ");
        }
      } catch (_e) { /* ignore — directMsg 会少几个字段但不影响主流程 */ }

      taskQueue.push({
        task: t,
        retryCount: 0,
        enqueuedAt: Date.now(),
        agentMsg: buildAgentNotification(t),
        directMsg: buildDirectNotification(t),
      });
      console.log(`[CM:collect]   → 入队 ${taskType}: 「${title}」(${meetingId})`);
    }

    // 持久化 notifiedMeetings（避免重启丢失）
    if (notifiedMeetings.size > 200) {
      const arr = [...notifiedMeetings];
      const keep = arr.slice(arr.length - 100);
      notifiedMeetings.clear();
      keep.forEach(id => notifiedMeetings.add(id));
    }
    if (notifiedMeetings.size > 0) {
      saveNotifiedMeetings([...notifiedMeetings]);
    }

    console.log(`[CM:collect] 收集完成，队列长度=${taskQueue.length}`);
    return []; // collectTasks 不产生 fallback 消息，由 processQueue 处理
  }

  /**
   * processQueue: 从队列逐条取出处理
   * 每条：sessions_send → 提取 reply → message tool 分发
   * 失败则 retryCount++ 留在队列；超过 MAX_RETRY 或 OFFLINE_TIMEOUT 则放弃
   */
  async function processQueue(): Promise<void> {
    if (isProcessingQueue) return;
    if (taskQueue.length === 0) return;
    isProcessingQueue = true;

    console.log(`[CM:queue] === 开始处理队列，共 ${taskQueue.length} 条 ===`);
    const now = Date.now();

    // 每轮只处理一条，5s 后 setInterval 自动处理下一条
    // 避免 agent 合并处理多条通知导致后续 reply 为空
    {
      const item = taskQueue[0];
      const t = item.task;
      const meetingId = t.meeting_id;
      const title = t.title ?? "未知会议";
      const taskType = t.task_type;
      const ageMs = now - item.enqueuedAt;

      // ---- Agent Offline 检测：入队超过 10 分钟未处理 ----
      if (ageMs >= OFFLINE_TIMEOUT_MS) {
        console.log(`[CM:queue] 任务超时 ${Math.round(ageMs / 60000)}min，上报 AGENT_OFFLINE: ${taskType}(${meetingId?.slice(-8)}) 「${title}」`);
        taskQueue.shift(); // 移出队列

        // 上报 REJECT + 原因说明
        try {
          await apiClient.submitAvailability(meetingId, {
            response_type: "REJECT",
            available_slots: [],
            preference_note: "Agent offline - 用户 Agent 在 10 分钟内未能响应此任务",
          });
          console.log(`[CM:queue] AGENT_OFFLINE 上报成功: ${meetingId?.slice(-8)}`);
        } catch (err) {
          console.error(`[CM:queue] AGENT_OFFLINE 上报失败: ${meetingId?.slice(-8)}: ${(err as Error)?.message}`);
        }

        // INITIAL_SUBMIT 不通知用户（静默处理），其他类型通知
        if (taskType !== "INITIAL_SUBMIT") {
          const offlineMsg = `⚠️ 会议「${title}」因 Agent 离线超时（10 分钟），已自动拒绝。如需参加请重新协商。`;
          await pushToExtraChannels(offlineMsg);
          pendingNotifications.push(offlineMsg);
        }

        notifiedMeetings.add(`${meetingId}:${taskType}`);
        saveNotifiedMeetings([...notifiedMeetings]);
        // Offline 处理完不 return，让下面 isProcessingQueue = false 执行
      } else {

      // ---- 正常处理：sessions_send → 提取 reply → message tool 分发 ----
      console.log(`[CM:queue] 处理: ${taskType}(${meetingId?.slice(-8)}) 「${title}」 retry=${item.retryCount} age=${Math.round(ageMs / 1000)}s`);

      const sendStartTs = Date.now(); // 用于 transcript 补捞的时间基线
      const { ok: mainOk, reply, timedOut, agentTriggered } = await sendViaSessionsSend(item.agentMsg);

      if (!mainOk) {
        // ---- sessions_send 失败 ----

        // 特殊处理：agent 已触发但 WS 断开 (status:"error")
        // agent 可能仍在执行，不应盲目重试（会重复触发 agent turn）
        // 尝试从 transcript 补捞 reply
        if (agentTriggered) {
          console.log(`[CM:queue] agent 已触发但连接断开，尝试从 transcript 补捞 reply...`);
          taskQueue.shift(); // 移出队列，不重试（避免重复 agent turn）
          const sk = sessionCtx.sessionKey ?? "agent:main:main";
          const polledReply = await pollReplyFromTranscript(sk, meetingId, taskType, sendStartTs, 40000, 5000);
          const channelMsg = polledReply ?? (item.directMsg.trim() ? item.directMsg : undefined);
          const source = polledReply ? "transcript 补捞" : "directMsg (补捞失败)";
          if (channelMsg) {
            console.log(`[CM:queue] 推送到额外渠道 (${source}): ${channelMsg.length}字`);
            await pushToExtraChannels(channelMsg);
          }
        } else {
          // 非 agent-triggered 的失败（forbidden/404/网络错误）→ 正常重试
          item.retryCount++;
          if (item.retryCount >= MAX_RETRY) {
            console.error(`[CM:queue] 超过最大重试次数(${MAX_RETRY})，fallback: ${taskType}(${meetingId?.slice(-8)})`);
            taskQueue.shift();
            if (taskType === "INITIAL_SUBMIT") {
              console.log(`[CM:queue] INITIAL_SUBMIT 失败，不推送到用户渠道，等下次轮询重新入队`);
              submittedMeetings.delete(meetingId);
            } else {
              // fallback：用构建好的 directMsg 推送到渠道 + pendingNotifications
              if (item.directMsg.trim()) {
                await pushToExtraChannels(item.directMsg);
                pendingNotifications.push(item.directMsg);
              } else {
                console.log(`[CM:queue] directMsg 为空，跳过 fallback 推送`);
              }
            }
          } else {
            console.log(`[CM:queue] sessions_send 失败，留在队列等下次重试 (retry=${item.retryCount}/${MAX_RETRY})`);
          }
        }
      } else {
        // ---- sessions_send 成功，移出队列 ----
        taskQueue.shift();
        console.log(`[CM:queue] sessions_send 成功`);

        // 统一走 transcript 补捞拿完整 agent reply（sessions_send 的 reply 不可靠）
        // directMsg 仅作为补捞失败时的兜底
        {
          console.log(`[CM:queue] 从 transcript 补捞完整 reply...`);
          const sk = sessionCtx.sessionKey ?? "agent:main:main";
          const polledReply = await pollReplyFromTranscript(sk, meetingId, taskType, sendStartTs, 40000, 5000);
          const channelMsg = polledReply ?? (item.directMsg.trim() ? item.directMsg : undefined);
          const channelSource = polledReply ? "transcript 补捞" : "directMsg (补捞失败)";

          if (channelMsg) {
            console.log(`[CM:queue] 推送到额外渠道 (${channelSource}): ${channelMsg.length}字`);
            await pushToExtraChannels(channelMsg);
          } else {
            console.log(`[CM:queue] 补捞失败且 directMsg 为空，跳过推送`);
          }
        }
      }
      } // close else (normal processing, not offline)
    } // close single-item block

    console.log(`[CM:queue] === 队列处理完成，剩余 ${taskQueue.length} 条 ===`);
    isProcessingQueue = false;
  }

  // ============================================================
  // 10. 轮询管理器
  // ============================================================
  const taskHandler = createCheckAndRespondTasksHandler(apiClient);
  const pollingManager = new PollingManager({
    intervalMs: pluginConfig.pollingIntervalMs,
    enabled: pluginConfig.autoRespond,
    onPoll: async () => {
      const result = await taskHandler({});
      const taskResults = (result as any).task_results ?? [];
      // 诊断：列出 API 返回的所有任务（含类型和 meeting_id）
      if (taskResults.length > 0) {
        console.log(`[CM:poll] API 返回 ${taskResults.length} 个任务: ${taskResults.map((t: any) => `${t.task_type}(${t.meeting_id?.slice(-8)})`).join(", ")}`);
      }
      const newTasks = taskResults.filter((t: any) => {
        const tt = t.task_type;
        const mid = t.meeting_id;
        // CONFIRMED/OVER：纯通知去重（用 mid:tt 组合 key，同一会议不同阶段独立通知）
        if (tt === "MEETING_CONFIRMED" || tt === "MEETING_OVER") {
          const dedupKey = `${mid}:${tt}`;
          const dup = notifiedMeetings.has(dedupKey);
          if (dup) console.log(`[CM:dedup] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 notifiedMeetings (${dedupKey})`);
          return !dup;
        }
        // FAILED：需要发起人决策，用 pendingDecisions 去重
        if (tt === "MEETING_FAILED") {
          const dup = pendingDecisions.has(mid);
          if (dup) console.log(`[CM:dedup] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 pendingDecisions`);
          return !dup;
        }
        // INITIAL_SUBMIT：已提交过的不再重试
        if (tt === "INITIAL_SUBMIT") {
          const dup = submittedMeetings.has(mid);
          if (dup) console.log(`[CM:dedup] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 submittedMeetings`);
          return !dup;
        }
        // COUNTER_PROPOSAL：等待用户决策的不再重复通知
        if (tt === "COUNTER_PROPOSAL") {
          const dup = pendingDecisions.has(mid);
          if (dup) console.log(`[CM:dedup] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 pendingDecisions`);
          return !dup;
        }
        // 其它未知类型：用 mid:tt 组合 key 去重
        return !notifiedMeetings.has(`${mid}:${tt}`);
      });
      if (newTasks.length > 0) {
        console.log(`[CM:poll] 轮询发现 ${newTasks.length} 个新待办任务: ${newTasks.map((t: any) => `${t.task_type}(${t.meeting_id?.slice(-8)})`).join(", ")}`);
      }
      return { ...(result as any), task_results: newTasks, pending_count: newTasks.length };
    },
    onAutoRespond: collectTasks,
    onNotifyUser: (messages: string[]) => {
      // fallback 通知（collectTasks 不产生 fallback，保留接口兼容）
      pendingNotifications.push(...messages);
    },
  });

  // 队列处理器：独立于轮询，5s 间隔逐条处理
  let queueTimer: ReturnType<typeof setInterval> | null = null;

  // ============================================================
  // 11. 插件加载时：有 Token 立即启动轮询
  // ============================================================
  function startQueueProcessor() {
    if (queueTimer) return;
    queueTimer = setInterval(() => processQueue(), QUEUE_PROCESS_INTERVAL);
    console.log(`[CM:queue] 队列处理器启动，间隔 ${QUEUE_PROCESS_INTERVAL}ms`);
  }

  function stopQueueProcessor() {
    if (queueTimer) {
      clearInterval(queueTimer);
      queueTimer = null;
      console.log(`[CM:queue] 队列处理器已停止`);
    }
  }

  // 注意：轮询不在这里启动。等 gateway_start 钩子触发后再启动，
  // 确保 ensurePluginConfig 先执行（plugins.allow + gateway.tools.allow 需要就绪）。
  // 如果 gateway_start 不触发（旧版 SDK），由 registerService.start 兜底。
  if (apiClient.getToken()) {
    console.log("[CM:init] 有已保存的 Token，轮询将在 gateway_start 后启动");
  } else {
    console.log("[CM:init] 无 Token，轮询不启动（等待用户绑定邮箱）");
  }

  // ============================================================
  // 12. registerService
  // ============================================================
  api.registerService?.({
    id: "clawmeeting-polling",
    start: (_ctx: any) => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[CM:lifecycle] Service start: 启动轮询。");
        pollingManager.start();
        startQueueProcessor();
      }
    },
    stop: (_ctx: any) => {
      console.log("[CM:lifecycle] Service stop: 停止轮询。");
      pollingManager.stop();
      stopQueueProcessor();
    },
  });

  // ============================================================
  // 13. 生命周期钩子（使用 SDK 标准 hook 名称）
  // ============================================================
  console.log(`[CM:init] 注册生命周期钩子... api.on 类型=${typeof api.on}`);
  api.on?.(
    "gateway_start",
    () => {
      // 三保险：gateway 就绪后确认插件配置
      ensureAllConfig();
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[CM:lifecycle] gateway_start: 启动轮询。");
        pollingManager.start();
        startQueueProcessor();
      }
    },
  );

  console.log("[CM:init] ✅ 钩子注册: gateway_start");
  api.on?.(
    "gateway_stop",
    () => {
      pollingManager.stop();
      stopQueueProcessor();
      console.log("[CM:lifecycle] gateway_stop: 停止轮询。");
    },
  );
  console.log("[CM:init] ✅ 钩子注册: gateway_stop");

  // ============================================================
  // 14. 暴露运行时单例到 _shared（供工具 execute 闭包引用）
  // ============================================================
  _shared.apiClient = apiClient;
  _shared.pollingManager = pollingManager;
  _shared.pendingDecisions = pendingDecisions;
  _shared.submittedMeetings = submittedMeetings;
  _shared.refreshCredentials = refreshCredentials;
  _shared.startQueueProcessor = startQueueProcessor;
  console.log("[CM:init] _shared 运行时上下文已就绪");

  // ============================================================
  // 15. CLI 命令
  // ============================================================
  api.registerCli?.(
    ({ program }: any) => {
      program
        .command("clawmeeting-status")
        .description("查看 ClawMeeting 插件状态")
        .action(() => {
          const creds = loadCredentials();
          const session = loadSession();
          console.log("=== ClawMeeting Meeting Negotiator ===");
          console.log(`服务端地址: ${pluginConfig.serverUrl}`);
          console.log(`轮询间隔: ${pluginConfig.pollingIntervalMs}ms`);
          console.log(`自动响应: ${pluginConfig.autoRespond ? "开启" : "关闭"}`);
          console.log(`轮询状态: ${pollingManager.isRunning() ? "运行中" : "已停止"}`);
          console.log(`已通知会议数: ${notifiedMeetings.size}`);
          console.log(`主动推送: ${gatewayToken ? "可用 (分流: message + sessions_send)" : "不可用"}`);
          if (creds?.email) {
            console.log(`已绑定邮箱: ${creds.email}`);
          }
          if (session?.sessionKey) {
            console.log(`Session: ${session.sessionKey}`);
          }
        });
    },
    { commands: ["clawmeeting-status"] },
  );

  // ============================================================
  // 16. before_prompt_build
  // ============================================================
  api.on?.(
    "before_prompt_build",
    (_event: any, ctx: any) => {
      try {
      // Session 捕获（只捕获主 session，过滤 cron/sub-agent/run 等临时 session）
      // SDK: event = { prompt, messages }, ctx = { agentId, sessionKey, sessionId, channelId, ... }
      const sessionKey = ctx?.sessionKey;
      const channel = ctx?.channelId;
      const agentId = ctx?.agentId;
      const peerId = ctx?.peerId;

      // 判断是否为主 session：排除 cron、subagent、run 等临时 session
      const isMainSession = sessionKey
        && !sessionKey.includes(":cron:")
        && !sessionKey.includes(":run:")
        && !sessionKey.includes(":subagent:");

      console.log(`[CM:hook] before_prompt_build: sessionKey=${sessionKey}, channel=${channel ?? "null"}, agentId=${agentId ?? "null"}, peerId=${peerId ?? "null"}, isMain=${isMainSession}`);

      if (isMainSession) {
        // 主 session 捕获（webchat）
        // channel 为 null 时从 sessionKey 解析渠道名，防止飞书/Telegram 的 before_prompt_build 不传 channel 时污染主 session
        const channelFromKey = sessionKey.split(":")[2] ?? "";
        const effectiveChannel = channel || channelFromKey;
        const isWebchat = !effectiveChannel || WEBCHAT_CHANNELS.has(effectiveChannel);
        const normalizedChannel = effectiveChannel || "webchat";
        if (isWebchat && (sessionKey !== sessionCtx?.sessionKey || normalizedChannel !== (sessionCtx?.channel || "webchat"))) {
          const oldKey = sessionCtx.sessionKey;
          sessionCtx = { sessionKey, channel: normalizedChannel };
          saveSession(sessionCtx);
          console.log(`[CM:hook] 主 session 更新: ${oldKey} → ${sessionKey} (channel=${channel ?? "webchat"})`);
        }

        // 额外渠道 session 捕获（通用：Telegram/飞书/Discord 等）
        if (channel && !WEBCHAT_CHANNELS.has(channel)) {
          const existing = extraChannels.get(channel);
          if (!existing || existing.sessionKey !== sessionKey) {
            const oldKey = existing?.sessionKey ?? "null";
            const ctx: SessionContext = { sessionKey, channel };
            extraChannels.set(channel, ctx);
            saveChannelCtx(channel, ctx);
            console.log(`[CM:hook] 渠道 ${channel} session 更新: ${oldKey} → ${sessionKey}`);
          }
        }
      }

      // 非主 session 不注入 system prompt，节省 token
      if (!isMainSession) {
        console.log(`[CM:hook] 非主 session，返回空（不注入 system prompt）`);
        return {};
      }

      // System prompt 注入（用内存变量，避免每次读磁盘）
      const isBound = !!apiClient.getToken();

      const systemPromptAddon = isBound
        ? [
            "[ClawMeeting Assistant Ready]",
            `Bound email: ${savedCreds?.email ?? "unknown"}. Background polling is active.`,
            "",
            "[ClawMeeting GLOBAL RULE — MANDATORY]",
            "NEVER call any external HTTP API, REST endpoint, or service directly for ANY meeting-related operation.",
            "ALL meeting actions MUST go through the provided ClawMeeting tools. No exceptions.",
            "Available tools: bind_identity, verify_email_code, initiate_meeting, check_and_respond_tasks, list_meetings.",
            "",
            "Tool usage guide:",
            "- User wants to schedule/create a meeting → call `initiate_meeting`",
            "- User wants to check invitations or pending tasks → call `check_and_respond_tasks` (no params)",
            "- User wants to see their meetings → call `list_meetings`",
            "- User makes a decision (accept/reject/new proposal) → call `check_and_respond_tasks` with response",
            "",
            "When initiating a meeting, collect from the user (natural language, you parse):",
            "  - Meeting title",
            "  - Duration (e.g. 'half an hour', '1 hour')",
            "  - Invitee email(s)",
            "  - Organizer's available time slots (e.g. 'tomorrow 2pm to 5pm')",
            "If any of the above is missing, ask the user — do not assume or fabricate.",
            "Convert natural language time descriptions to 'YYYY-MM-DD HH:MM-HH:MM' format.",
            "If your memory contains the user's meeting preferences (e.g. dislikes early meetings), fill in preference_note. Otherwise leave it empty.",
            "Once you have all required info, call `initiate_meeting` immediately — do NOT call any external API.",
            "If `initiate_meeting` result indicates any invitee is not registered, explicitly remind the user:",
            "  The invitee xxx is not registered on ClawMeeting yet. Direct email collaboration will be supported in a future release. Stay tuned.",
            "",
            "Background behavior:",
            "- On [ClawMeeting Meeting Invitation]: follow this exact order to determine available time slots. Prioritize autonomous submission first:",
            "  Step 1: Check if the user has a connected calendar. If yes, query it first to get their real schedule.",
            "  Step 2: Check your memory for schedule info the user has mentioned (business trips, appointments, etc.).",
            "  Step 3: Combine calendar + memory with the organizer's proposed slots. If you can decide reliably, call `check_and_respond_tasks` immediately.",
            "          (response_type=INITIAL with available_slots, or REJECT if clearly impossible).",
            "  Ask the user ONLY when information is insufficient, conflict judgement is ambiguous, or confidence is low.",
            "  Always provide a structured transparency report after processing; never reply with a one-line 'submitted/rejected' message.",
            "- On [ClawMeeting COUNTER_PROPOSAL]:",
            "  This is a counter-proposal notice. Present all coordinator suggestions in full and ask the user:",
            "  1. Accept -> call `check_and_respond_tasks` with response_type='ACCEPT_PROPOSAL'",
            "  2. Propose new slots -> user provides slots, call response_type='NEW_PROPOSAL' + available_slots",
            "  3. Reject -> call response_type='REJECT'",
            "- On [ClawMeeting MEETING_FAILED]:",
            "  This is a negotiation-failed notice. Present the failure reason in full and ask the user to choose one of three decisions:",
            "  1. Cancel meeting -> call `check_and_respond_tasks` with response_type='REJECT'",
            "  2. Retry with adjusted slots -> user provides new slots, call response_type='NEW_PROPOSAL' + available_slots",
            "  3. Change meeting setup (not only time), e.g., adjust duration, split the meeting, add/remove participants, or switch to online/offline/email.",
            "     For this type of change, first call `check_and_respond_tasks` with response_type='REJECT' to close the current failed negotiation, then use `initiate_meeting` to create a new meeting.",
            "- On [ClawMeeting MEETING_CONFIRMED]:",
            "  This is a meeting-confirmed notice. Present all information in full to the user:",
            "  Meeting title, confirmed time, duration, meeting link (if any). Do not omit details or simplify.",
            "- On [ClawMeeting MEETING_OVER]:",
            "  This is a meeting-cancelled notice. Inform the user the meeting was cancelled and show the title and reason.",
            "",
            "[ClawMeeting CRITICAL RULE — Tool Execution Required]",
            "When the user makes ANY decision about a meeting (accept, reject, cancel, retry, new times, or meeting-setup changes),",
            "you MUST call tools to execute it. NEVER just acknowledge verbally.",
            "For setup-change decisions (duration/split/participants/format), execute with: check_and_respond_tasks(REJECT) + initiate_meeting(new setup).",
            "Keyword mapping: 'cancel'/'drop'/'reject'/'not attending' -> REJECT.",
            "'accept'/'agree'/'works' -> ACCEPT_PROPOSAL.",
            "'change time'/'retry' -> ask user for new slots, then NEW_PROPOSAL.",
            "'make it 30 minutes'/'split into two meetings'/'add another participant'/'switch to email' -> setup-change flow (REJECT current + initiate new).",
            "If in doubt, call the tool. A verbal-only response is ALWAYS wrong for meeting decisions.",
          ].join("\n")
        : [
            "[ClawMeeting Assistant - Setup Required]",
            "The user has not bound their email yet. Follow this EXACT flow — never call any external API directly:",
            "Step 1: Ask the user for their email address (if not already provided).",
            "Step 2: Call the `bind_identity` tool with their email. This sends a verification code to their inbox.",
            "Step 3: Ask the user to check their email and provide the 6-digit code.",
            "Step 4: Call the `verify_email_code` tool with the email + code. This completes binding and starts background polling.",
            "RULES:",
            "- NEVER call any HTTP endpoint or external API directly.",
            "- ALWAYS use `bind_identity` to send the code (do NOT tell the user to go elsewhere).",
            "- ALWAYS use `verify_email_code` to verify (do NOT manually validate the code yourself).",
            "- If the user provides both email and code in one message, still call bind_identity first, then verify_email_code.",
            "- After verify_email_code succeeds, inform the user that binding is complete and explain what ClawMeeting can do.",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // 后台轮询推送的通知 → 注入 prependContext，Agent 看到后自然语言转述给用户
      // 用户在 webchat 里不会看到原始通知文本，只看到 Agent 的回复
      if (pendingNotifications.length > 0) {
        console.log(`[CM:hook] 注入 prependContext: ${pendingNotifications.length} 条待推送通知（fallback 路径）`);
        result.prependContext = [
          "[ClawMeeting Important Notifications]",
          ...pendingNotifications,
        ].join("\n");
        pendingNotifications = [];
      }

      console.log(`[CM:hook] 返回: appendSystemContext=${isBound ? "已绑定模板" : "未绑定模板"}, prependContext=${result.prependContext ? "有" : "无"}`);
      return result;
      } catch (err) {
        console.error(`[CM:hook] before_prompt_build 异常: ${(err as Error)?.message}`);
        return {};
      }
    },
    { priority: 5 },
  );
  console.log("[CM:init] ✅ 钩子注册: before_prompt_build (priority=5)");

  const channelList = extraChannels.size > 0 ? [...extraChannels.keys()].join(",") : "无";
  console.log(`[CM:init] ===== ClawMeeting v${PKG_VERSION} 初始化完成 =====`);
  console.log(`[CM:init]   session=${sessionCtx.sessionKey}`);
  console.log(`[CM:init]   额外渠道=[${channelList}]`);
  console.log(`[CM:init]   polling=${pollingManager.isRunning() ? "运行中" : "未启动（等 gateway_start）"}`);
  console.log(`[CM:init]   gateway=${gatewayToken ? "可用" : "不可用"}`);
  console.log(`[CM:init]   api.registerTool=${typeof api.registerTool}`);
  console.log(`[CM:init]   api.on=${typeof api.on}`);
  console.log(`[CM:init]   api.registerService=${typeof api.registerService}`);
  console.log(`[CM:init] ================================`);

  // ============================================================
  // 重启后欢迎消息注入
  // ============================================================
  // 场景：用户执行 "openclaw plugins install clawmeeting 然后介绍插件功能"
  // → 首次安装触发 gateway 重启 → 用户的请求被中断
  // → 重启后检测到 flag → 主动注入欢迎消息，同时验证 sessions_send 可用
  if (existsSync(RESTART_WELCOME_FLAG)) {
    try { unlinkSync(RESTART_WELCOME_FLAG); } catch { /* ignore */ }
    console.log("[CM:init] 🎉 检测到 restart-welcome flag，将注入欢迎消息...");

    const readmePath = join(__dirname_esm, "README.md");
    const welcomePrompt = [
      "[System Notice] ClawMeeting plugin is installed and configured (gateway has restarted to load the new configuration).",
      `Please read the plugin guide at ${readmePath}, then give the user a friendly and concise introduction to its core capabilities and usage.`,
    ].join("\n");

    setTimeout(async () => {
      const result = await sendViaSessionsSend(welcomePrompt);
      if (result.ok) {
        console.log("[CM:init] ✅ 欢迎消息注入成功，sessions_send 可用");
      } else {
        console.warn("[CM:init] ⚠️ 欢迎消息注入失败，sessions_send 可能未就绪");
      }
    }, 8000); // 等 gateway 完全启动 + sessions 就绪
  }
}

