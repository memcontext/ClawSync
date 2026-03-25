// ============================================================
// ClawMeeting Plugin - 入口文件
// 架构设计：
//   1. 插件加载时：恢复 Token → 有 Token 则立即启动轮询
//   2. 各状态处理：
//      COLLECTING    → 通知 Agent，由 Agent 根据记忆和日历提交空闲时间
//      ANALYZING     → 跳过（等服务端分析完）
//      NEGOTIATING   → 通知 Agent 处理协商
//      CONFIRMED     → 主动推送通知给用户（含完整会议信息 + 虚拟会议号）
//      FAILED        → 主动推送通知给用户
//   3. 通知去重：持久化已通知 meeting_id
//   4. 主动推送：通过 gateway HTTP API 的 sessions_send 触发 agent 回合
// ============================================================

import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";
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
  loadAllChannelCtx,
  saveChannelCtx,
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
  serverUrl: "http://39.105.143.2:7010",
  pollingIntervalMs: 10000,
  autoRespond: true,
};


// ---- 从 manifest 读取插件 ID ----
function readPluginId(): string {
  try {
    const manifestPath = join(__dirname, "openclaw.plugin.json");
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    return manifest.id ?? "clawmeeting";
  } catch {
    return "clawmeeting";
  }
}

// ---- 单例守卫：防止框架多次调用 register 导致重复加载 ----
let _registered = false;

export default function register(api: any) {
  if (_registered) {
    return;
  }
  _registered = true;

  const PLUGIN_ID = readPluginId();

  // ============================================================
  // 1. 读取插件配置
  // ============================================================
  const pluginConfig: ClawMeetingPluginConfig = {
    ...DEFAULT_CONFIG,
    ...(api.config?.plugins?.entries?.[PLUGIN_ID]?.config ?? {}),
  };
  console.log(`[${PLUGIN_ID}] 插件配置: serverUrl=${pluginConfig.serverUrl}`);

  // 初始化存储目录
  initStorage(PLUGIN_ID);

  // ============================================================
  // 2. 初始化 API Client + 恢复 Token
  // ============================================================
  const apiClient = new ClawMeetingApiClient(pluginConfig.serverUrl);

  const savedCreds = loadCredentials();
  if (savedCreds?.token) {
    apiClient.setToken(savedCreds.token);
    console.log(`[ClawMeeting] 已恢复身份凭证: ${savedCreds.email} (user_id: ${savedCreds.user_id})`);
  }

  // ============================================================
  // 3. Session 管理
  // ============================================================
  let sessionCtx: SessionContext = loadSession() ?? { sessionKey: "agent:main:main" };
  if (sessionCtx.sessionKey) {
    console.log(`[ClawMeeting] session: ${sessionCtx.sessionKey}`);
  }

  // ============================================================
  // 3b. 多渠道叠加推送：自动发现所有活跃的非主 session 渠道
  // 优先级：已捕获的 session > 从 pairing allow store 自动发现
  // ============================================================
  const extraChannels: Map<string, SessionContext> = loadAllChannelCtx();
  if (extraChannels.size > 0) {
    console.log(`[ClawMeeting] 已恢复 ${extraChannels.size} 个渠道: ${[...extraChannels.keys()].join(", ")}`);
  }

  // 自动发现：遍历 api.config.channels，找 enabled 的渠道，读 pairing allow store
  // 排除 webchat（主 session 已覆盖）
  const WEBCHAT_CHANNELS = new Set(["webchat", "web", "main"]);
  const channelsConfig = api.config?.channels ?? {};
  for (const [channelName, channelCfg] of Object.entries(channelsConfig)) {
    if (WEBCHAT_CHANNELS.has(channelName)) continue;
    if (!(channelCfg as any)?.enabled) continue;
    if (extraChannels.has(channelName)) continue; // 已有捕获的 session，跳过

    // 尝试读取 pairing allow store: ~/.openclaw/credentials/{channel}-default-allowFrom.json
    try {
      const allowStorePath = join(homedir(), ".openclaw", "credentials", `${channelName}-default-allowFrom.json`);
      if (existsSync(allowStorePath)) {
        const storeData = JSON.parse(readFileSync(allowStorePath, "utf-8"));
        const allowFrom: string[] = storeData?.allowFrom ?? [];
        if (allowFrom.length > 0) {
          const targetId = allowFrom[0];
          const ctx: SessionContext = {
            sessionKey: `agent:main:${channelName}:direct:${targetId}`,
            channel: channelName,
          };
          extraChannels.set(channelName, ctx);
          saveChannelCtx(channelName, ctx);
          console.log(`[ClawMeeting] 自动发现 ${channelName} 目标: ${targetId} (from pairing allow store)`);
        }
      }
    } catch (err) {
      console.log(`[ClawMeeting] 读取 ${channelName} allow store 失败: ${err}`);
    }
  }

  // ============================================================
  // 4. Gateway 认证 Token（用于主动推送消息）
  // ============================================================
  const gatewayPort = api.config?.gateway?.port ?? 18789;
  const gatewayToken = api.config?.gateway?.auth?.token
    ?? process.env.OPENCLAW_GATEWAY_TOKEN
    ?? null;

  if (gatewayToken) {
    console.log("[ClawMeeting] 已获取 gateway token，支持主动推送通知");
  } else {
    console.log("[ClawMeeting] 未获取 gateway token，通知将在用户下次交互时展示");
  }

  // ============================================================
  // 5. 主动推送消息
  // 分流策略：
  //   纯通知（不需要 LLM）→ message tool 直接发给用户，零 announce 风险
  //   需要 Agent 处理     → sessions_send 触发 agent turn
  //
  // message tool 仅在正式 channel（telegram/feishu/discord 等）可用
  // webchat 不支持 message tool → fallback 到 sessions_send
  // ============================================================

  // getDirectMessageChannel 已由 parseChannelFromSessionKey 替代

  /**
   * 解析任意 sessionKey 的 channel + target（用于 message tool 直发）
   */
  function parseChannelFromSessionKey(sk: string | undefined): { channel: string; target: string } | null {
    if (!sk) return null;
    const rawParts = sk.split(":").filter(Boolean);
    const parts = rawParts.length >= 3 && rawParts[0] === "agent" ? rawParts.slice(2) : rawParts;
    if (parts.length < 3) return null;
    const [channelRaw, kind, ...rest] = parts;
    if (!channelRaw || channelRaw === "webchat" || channelRaw === "main") return null;
    if (kind !== "group" && kind !== "channel" && kind !== "dm" && kind !== "direct") return null;
    const restJoined = rest.join(":");
    const id = restJoined.replace(/:(topic|thread):\d+$/, "").trim();
    if (!id) return null;
    return { channel: channelRaw, target: id };
  }

  /**
   * 通过 message tool 直接发消息到指定 channel（不经过 LLM）
   */
  async function sendViaMessageTool(channel: string, target: string, message: string): Promise<boolean> {
    try {
      const res = await fetch(`http://127.0.0.1:${gatewayPort}/tools/invoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${gatewayToken}`,
        },
        body: JSON.stringify({
          tool: "message",
          args: { action: "send", channel, target, message },
        }),
      });
      if (res.ok) {
        console.log(`[ClawMeeting] message tool 发送成功 (${channel}:${target})`);
        return true;
      }
      console.log(`[ClawMeeting] message tool 失败 (${channel}): ${res.status}`);
      return false;
    } catch (err) {
      console.log(`[ClawMeeting] message tool 异常 (${channel}): ${err}`);
      return false;
    }
  }

  /**
   * 通过 sessions_send 发送到指定 session，触发 agent turn
   */
  async function sendViaSessionsSend(message: string, sessionKey?: string): Promise<{ ok: boolean }> {
    const sk = sessionKey ?? sessionCtx.sessionKey ?? "agent:main:main";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

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

      if (res.ok) {
        console.log(`[ClawMeeting] sessions_send 成功 → ${sk}`);
        return { ok: true };
      } else {
        const body = await res.text();
        console.error(`[ClawMeeting] sessions_send 失败 (${sk}): ${res.status} ${body}`);
        return { ok: false };
      }
    } catch (err) {
      clearTimeout(timeout);
      const errMsg = err instanceof Error ? err.message : String(err);
      console.log(`[ClawMeeting] sessions_send 未完成 (${sk}): ${errMsg}`);
      return { ok: false };
    }
  }

  /**
   * 推送需要 Agent 静默处理的消息（INITIAL_SUBMIT 等）
   * 仅走 sessions_send 触发 agent turn，不推送给用户
   */
  async function pushAgentMessage(message: string): Promise<boolean> {
    if (!gatewayToken) return false;
    const { ok } = await sendViaSessionsSend(message);
    return ok;
  }


  // ============================================================
  // 6. 通知去重 + 提交去重
  // ============================================================
  const notifiedMeetings = new Set<string>(loadNotifiedMeetings());
  // 记录已成功提交但仍在 COLLECTING 的会议，避免无限重试
  const submittedMeetings = new Set<string>();
  // 等待用户决策的会议（COUNTER_PROPOSAL 已通知用户，等回复）
  const pendingDecisions = new Set<string>(loadPendingDecisions());
  if (notifiedMeetings.size > 0) {
    console.log(`[ClawMeeting] 已恢复 ${notifiedMeetings.size} 个已通知会议记录`);
  }
  if (pendingDecisions.size > 0) {
    console.log(`[ClawMeeting] 已恢复 ${pendingDecisions.size} 个等待用户决策的会议`);
  }

  // ============================================================
  // 7. 待推送通知队列（备用：主动推送失败时 fallback 到 prependContext）
  // ============================================================
  let pendingNotifications: string[] = [];

  // ============================================================
  // 8. 构建通知消息
  // ============================================================
  /** 构建通知：服务端 message 字段已包含完整格式化文本（含会议链接），直接使用 */
  function buildNotification(t: any): string {
    const serverMessage = t.message ?? "";
    if (serverMessage) return `[ClawMeeting ${t.task_type}]\n${serverMessage}`;
    // fallback: 无 message 时用基本信息
    return `[ClawMeeting ${t.task_type ?? ""}] Meeting: "${t.title ?? "未知会议"}"`;
  }

  // ============================================================
  // 9. 自动响应逻辑
  // 关键设计：先收集所有通知 + 同步去重 + 持久化，最后一次性推送
  // 避免多个 system event 并发触发多个 agent turn 导致重复
  // ============================================================
  async function autoRespondToTasks(tasks: unknown[]): Promise<string[]> {
    const userMessages: string[] = [];
    const notifications: string[] = []; // 收集本轮所有通知，最后批量发送

    for (const task of tasks) {
      const t = task as any;
      const meetingId = t.meeting_id;
      const title = t.title ?? "未知会议";
      const taskType = t.task_type;

      // ---- INITIAL_SUBMIT：通知 Agent，由 Agent 根据记忆和日历处理 ----
      if (taskType === "INITIAL_SUBMIT") {
        if (submittedMeetings.has(meetingId)) continue;
        if (pendingDecisions.has(meetingId)) continue;

        submittedMeetings.add(meetingId);
        // 注意：不加入 pendingDecisions，否则后续 COUNTER_PROPOSAL 会被去重跳过

        const notifyLines = [
          `[ClawMeeting Meeting Invitation]`,
          `Meeting: "${title}"`,
          `Meeting ID: ${meetingId}`,
          `Organizer: ${t.initiator ?? "unknown"}`,
          `Duration: ${t.duration_minutes ?? "unknown"} minutes`,
        ];

        // Fetch meeting detail to include initiator's submitted time slots
        try {
          const detail = await apiClient.getMeetingDetail(meetingId);
          const submittedParticipants = detail.participants.filter(
            (p: any) => p.has_submitted && p.latest_slots?.length > 0,
          );
          if (submittedParticipants.length > 0) {
            notifyLines.push("", "Submitted available slots:");
            for (const p of submittedParticipants) {
              notifyLines.push(`  ${p.email} (${p.role}): ${p.latest_slots.join(", ")}`);
            }
          }
        } catch (_e) {
          // Ignore detail fetch failures, continue with basic info
        }

        notifications.push(notifyLines.join("\n"));
        console.log(
          `[ClawMeeting] 会议「${title}」(${meetingId}) 通知 Agent 处理`,
        );
        continue;
      }

      // ---- COUNTER_PROPOSAL：通知用户，等待决策 ----
      if (taskType === "COUNTER_PROPOSAL") {
        // 已通知过且在等待用户回复，跳过
        if (pendingDecisions.has(meetingId)) continue;

        // 标记为等待用户决策
        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        const roundCount = t.round_count ?? 0;
        const coordinatorMessage = t.message ?? "协调方发来了协商建议。";

        const notifyLines = [
          `[ClawMeeting COUNTER_PROPOSAL]`,
          `Meeting: "${title}"`,
          `Negotiation round: ${roundCount}`,
          `${coordinatorMessage}`,
        ];
        notifications.push(notifyLines.join("\n"));

        console.log(
          `[ClawMeeting] 会议「${title}」(${meetingId}) 第${roundCount}轮协商，已通知用户等待决策`,
        );
        continue;
      }

      // ---- MEETING_FAILED：需要发起人决策（取消 or 重新发起）----
      if (taskType === "MEETING_FAILED") {
        if (pendingDecisions.has(meetingId)) continue;

        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        const notifyLines = [
          `[ClawMeeting MEETING_FAILED]`,
          `Meeting: "${title}"`,
          `${t.message ?? "Meeting negotiation failed."}`,
        ];
        notifications.push(notifyLines.join("\n"));
        console.log(
          `[ClawMeeting] 会议「${title}」(${meetingId}) 协商失败，等待发起人决策`,
        );
        continue;
      }

      // ---- 其它类型（CONFIRMED/OVER 等）：纯通知，展示 message 内容 ----
      if (notifiedMeetings.has(meetingId)) continue;
      notifiedMeetings.add(meetingId);

      // 服务端 message 字段已包含完整格式化文本（含时间、时长、会议链接）
      notifications.push(buildNotification(t));
      console.log(`[ClawMeeting] 会议「${title}」(${meetingId}) 通知 type=${taskType}`);
    }

    // ==== 先持久化，再推送（确保不会因重启丢失去重状态）====
    // 限制 notifiedMeetings 大小，超过 200 条时清除最早的一半
    if (notifiedMeetings.size > 200) {
      const arr = [...notifiedMeetings];
      const keep = arr.slice(arr.length - 100);
      notifiedMeetings.clear();
      keep.forEach(id => notifiedMeetings.add(id));
    }
    if (notifiedMeetings.size > 0) {
      saveNotifiedMeetings([...notifiedMeetings]);
    }

    // ==== 推送策略 ====
    // 所有通知 sessions_send 到主 session + 每个额外渠道 session
    // agent 在每个 session 里自然处理并回复，用户在任何渠道都能看到 agent 的回复
    // INITIAL_SUBMIT 仅推主 session（agent 需要调用工具提交时段，不应重复执行）

    const silentMeetingIds: string[] = [];
    for (const n of notifications) {
      if (n.includes("[ClawMeeting Meeting Invitation]")) {
        const idMatch = n.match(/Meeting ID: (mtg_\w+)/);
        if (idMatch) silentMeetingIds.push(idMatch[1]);
      }
    }

    if (notifications.length === 0) return userMessages;

    const batchMsg = notifications.join("\n\n---\n\n");
    console.log(`[ClawMeeting] 推送 ${notifications.length} 条通知到主 session + ${extraChannels.size} 个额外渠道`);

    // ---- 主 session ----
    const { ok: mainOk } = await sendViaSessionsSend(batchMsg);
    if (!mainOk) {
      // 主 session 失败：INITIAL_SUBMIT 回滚让下次重试，其他放入 pendingNotifications
      for (const mid of silentMeetingIds) {
        submittedMeetings.delete(mid);
      }
      if (silentMeetingIds.length > 0) {
        console.log(`[ClawMeeting] 主 session 推送失败，已回滚 ${silentMeetingIds.length} 个 submittedMeetings`);
      }
      userMessages.push(...notifications.filter(n => !n.includes("[ClawMeeting Meeting Invitation]")));
    }

    // ---- 额外渠道：仅推用户可见通知（排除 INITIAL_SUBMIT）----
    // INITIAL_SUBMIT 需要 agent 调用工具提交时段，多个 session 同时执行会重复提交
    const userVisibleNotifications = notifications.filter(n => !n.includes("[ClawMeeting Meeting Invitation]"));
    if (userVisibleNotifications.length > 0) {
      const userMsg = userVisibleNotifications.join("\n\n---\n\n");
      const mainChannel = parseChannelFromSessionKey(sessionCtx.sessionKey);
      for (const [channelName, ctx] of extraChannels) {
        if (mainChannel && mainChannel.channel === channelName) continue;
        // sessions_send 到渠道 session，让 agent 在那个 session 里处理并回复
        sendViaSessionsSend(userMsg, ctx.sessionKey).catch(err =>
          console.log(`[ClawMeeting] ${channelName} session 推送异常: ${err}`),
        );
      }
    }

    return userMessages;
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
        console.log(`[ClawMeeting] API 返回 ${taskResults.length} 个任务: ${taskResults.map((t: any) => `${t.task_type}(${t.meeting_id?.slice(-8)})`).join(", ")}`);
      }
      const newTasks = taskResults.filter((t: any) => {
        const tt = t.task_type;
        const mid = t.meeting_id;
        // CONFIRMED/OVER：纯通知去重
        if (tt === "MEETING_CONFIRMED" || tt === "MEETING_OVER") {
          const dup = notifiedMeetings.has(mid);
          if (dup) console.log(`[ClawMeeting] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 notifiedMeetings`);
          return !dup;
        }
        // FAILED：需要发起人决策，用 pendingDecisions 去重
        if (tt === "MEETING_FAILED") {
          const dup = pendingDecisions.has(mid);
          if (dup) console.log(`[ClawMeeting] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 pendingDecisions`);
          return !dup;
        }
        // INITIAL_SUBMIT：已提交过的不再重试
        if (tt === "INITIAL_SUBMIT") {
          const dup = submittedMeetings.has(mid);
          if (dup) console.log(`[ClawMeeting] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 submittedMeetings`);
          return !dup;
        }
        // COUNTER_PROPOSAL：等待用户决策的不再重复通知
        if (tt === "COUNTER_PROPOSAL") {
          const dup = pendingDecisions.has(mid);
          if (dup) console.log(`[ClawMeeting] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 pendingDecisions`);
          return !dup;
        }
        // 其它未知类型：用 notifiedMeetings 去重，防止重复推送
        return !notifiedMeetings.has(mid);
      });
      if (newTasks.length > 0) {
        console.log(`[ClawMeeting] 轮询发现 ${newTasks.length} 个新待办任务: ${newTasks.map((t: any) => `${t.task_type}(${t.meeting_id?.slice(-8)})`).join(", ")}`);
      }
      return { ...(result as any), task_results: newTasks, pending_count: newTasks.length };
    },
    onAutoRespond: autoRespondToTasks,
    onNotifyUser: (messages: string[]) => {
      // fallback 通知（主动推送失败时才会有内容）
      pendingNotifications.push(...messages);
    },
  });

  // ============================================================
  // 11. 插件加载时：有 Token 立即启动轮询
  // ============================================================
  if (apiClient.getToken()) {
    console.log("[ClawMeeting] 有已保存的 Token，立即启动轮询。");
    pollingManager.start();
  }

  // ============================================================
  // 12. registerService
  // ============================================================
  api.registerService?.({
    id: "clawmeeting-polling",
    start: (_ctx: any) => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[ClawMeeting] Service start: 启动轮询。");
        pollingManager.start();
      }
    },
    stop: (_ctx: any) => {
      console.log("[ClawMeeting] Service stop: 停止轮询。");
      pollingManager.stop();
    },
  });

  // ============================================================
  // 13. 生命周期钩子（使用 SDK 标准 hook 名称）
  // ============================================================
  api.on?.(
    "gateway_start",
    () => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[ClawMeeting] gateway_start: 启动轮询。");
        pollingManager.start();
      }
    },
  );

  api.on?.(
    "gateway_stop",
    () => {
      pollingManager.stop();
      console.log("[ClawMeeting] gateway_stop: 停止轮询。");
    },
  );

  // ============================================================
  // 14. 注册 4 个 Tools
  // ============================================================

  api.registerTool({
    ...bindIdentitySchema,
    async execute(_id: string, params: any) {
      const handler = createBindIdentityHandler(apiClient, () => {
        if (!pollingManager.isRunning()) {
          pollingManager.start();
        }
      });
      const result = await handler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...verifyEmailCodeSchema,
    async execute(_id: string, params: any) {
      const handler = createVerifyEmailCodeHandler(apiClient, () => {
        if (!pollingManager.isRunning()) {
          pollingManager.start();
        }
      });
      const result = await handler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  api.registerTool({
    ...initiateMeetingSchema,
    async execute(_id: string, params: any) {
      const handler = createInitiateMeetingHandler(apiClient);
      const result = await handler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  const checkHandler = createCheckAndRespondTasksHandler(apiClient);
  api.registerTool({
    ...checkAndRespondTasksSchema,
    async execute(_id: string, params: any) {
      const result = await checkHandler(params);

      // 用户通过 Agent 提交了决策 → 清除等待状态，允许后续新轮次
      if (params.meeting_id && params.response_type && (result as any).success) {
        if (pendingDecisions.has(params.meeting_id)) {
          pendingDecisions.delete(params.meeting_id);
          savePendingDecisions([...pendingDecisions]);
          console.log(`[ClawMeeting] 会议 ${params.meeting_id} 用户已决策，清除等待状态`);
        }
        // 只在用户主动决策（非首次提交）时清除 submittedMeetings，允许新轮次重新自动提交
        // INITIAL 提交后不清除，否则会议仍在 COLLECTING 状态时轮询会重复推送
        if (params.response_type !== "INITIAL") {
          submittedMeetings.delete(params.meeting_id);
        }
      }

      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  const listHandler = createListMeetingsHandler(apiClient);
  api.registerTool({
    ...listMeetingsSchema,
    async execute(_id: string, params: any) {
      const result = await listHandler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

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
      // Session 捕获（只捕获主 session，过滤 cron/sub-agent/run 等临时 session）
      // SDK: event = { prompt, messages }, ctx = { agentId, sessionKey, sessionId, channelId, ... }
      const sessionKey = ctx?.sessionKey;
      const channel = ctx?.channelId;

      // 判断是否为主 session：排除 cron、subagent、run 等临时 session
      const isMainSession = sessionKey
        && !sessionKey.includes(":cron:")
        && !sessionKey.includes(":run:")
        && !sessionKey.includes(":subagent:");

      if (isMainSession) {
        if (sessionKey !== sessionCtx?.sessionKey || channel !== sessionCtx?.channel) {
          sessionCtx = { sessionKey, channel };
          saveSession(sessionCtx);
          console.log(`[ClawMeeting] session updated: ${sessionKey} channel=${channel}`);
        }

        // 额外渠道 session 自动捕获（叠加推送用）
        // 非 webchat/main 的渠道都记录下来
        if (channel && !WEBCHAT_CHANNELS.has(channel)) {
          const existing = extraChannels.get(channel);
          if (!existing || existing.sessionKey !== sessionKey) {
            const ctx: SessionContext = { sessionKey, channel };
            extraChannels.set(channel, ctx);
            saveChannelCtx(channel, ctx);
            console.log(`[ClawMeeting] ${channel} session captured: ${sessionKey}`);
          }
        }
      }

      // 非主 session 不注入 system prompt，节省 token
      if (!isMainSession) {
        return {};
      }

      // System prompt 注入（用内存变量，避免每次读磁盘）
      const isBound = !!apiClient.getToken();

      const systemPromptAddon = isBound
        ? [
            "[ClawMeeting Assistant Ready]",
            `Bound email: ${savedCreds?.email ?? "unknown"}. Background polling is active.`,
            "",
            "The user can schedule meetings by saying things like 'schedule a meeting with X',",
            "check invitations with 'any new meeting invitations?',",
            "or view meetings with 'show my meetings'.",
            "Refer to meetings by title in conversation — the user does not need to know the meeting ID.",
            "",
            "Required info when initiating a meeting (user describes in natural language, you parse):",
            "  - Meeting title",
            "  - Duration (e.g. 'half an hour', '1 hour')",
            "  - Invitee email(s)",
            "  - Organizer's available time slots (e.g. 'tomorrow 2pm to 5pm')",
            "If any of the above is missing, ask the user — do not assume.",
            "Convert natural language time descriptions to 'YYYY-MM-DD HH:MM-HH:MM' format.",
            "If your memory genuinely contains the user's meeting preferences (e.g. dislikes early meetings, no meetings on Fridays),",
            "fill in the preference_note parameter. If you have no such memory, leave it empty — never fabricate.",
            "",
            "Background behavior:",
            "- On [ClawMeeting Meeting Invitation]: follow this exact order to determine available time slots:",
            "  Step 1: Check if the user has a connected calendar. If yes, you MUST query it first to get their real schedule.",
            "  Step 2: Check your memory for any schedule info the user has mentioned (business trips, appointments, picking up kids, etc.).",
            "          Also check for meeting preferences (dislikes early meetings, no meetings on Fridays, etc.).",
            "  Step 3: Combine calendar data (if available) and memory clues with the organizer's proposed time slots to select suitable times and submit.",
            "  Only if the user has NO connected calendar AND your memory contains NO schedule info at all, ask the user for their availability.",
            "  Do not skip the calendar check. Do not ask the user if you already have enough info from calendar + memory.",
            "- On [ClawMeeting Negotiation Update]: the coordinator has sent a compromise proposal.",
            "  Present the proposal to the user and ask them to choose:",
            "  1. Accept → call check_and_respond_tasks with response_type='ACCEPT_PROPOSAL'",
            "  2. Propose new times → user provides times, you parse and call response_type='NEW_PROPOSAL' + available_slots",
            "  3. Reject → call response_type='REJECT' (records rejection, does not immediately terminate the meeting)",
            "- On [ClawMeeting Negotiation Failed]: inform the user (organizer) of the failure details and ask:",
            "  1. Cancel the meeting → call check_and_respond_tasks with response_type='REJECT'",
            "  2. Retry with adjusted times → user provides new times, call response_type='NEW_PROPOSAL' + available_slots",
            "- On [ClawMeeting Notification] (confirmation, cancellation, etc.):",
            "  Relay the notification content to the user in natural language.",
          ].join("\n")
        : [
            "[ClawMeeting Assistant - Setup Required]",
            "The user has not bound their identity yet. Guide them to provide their email to complete setup.",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // 后台轮询推送的通知 → 注入 prependContext，Agent 看到后自然语言转述给用户
      // 用户在 webchat 里不会看到原始通知文本，只看到 Agent 的回复
      if (pendingNotifications.length > 0) {
        result.prependContext = [
          "[ClawMeeting Important Notifications]",
          ...pendingNotifications,
        ].join("\n");
        pendingNotifications = [];
      }

      return result;
    },
    { priority: 5 },
  );

  console.log("[ClawMeeting] ClawMeeting Meeting Negotiator 插件已加载。");
}
