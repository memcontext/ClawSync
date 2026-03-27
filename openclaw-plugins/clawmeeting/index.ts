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
  console.log(`[CM:init] 插件配置: serverUrl=${pluginConfig.serverUrl}, pollingInterval=${pluginConfig.pollingIntervalMs}ms, autoRespond=${pluginConfig.autoRespond}`);

  // 初始化存储目录
  initStorage(PLUGIN_ID);

  // ============================================================
  // 2. 初始化 API Client + 恢复 Token
  // ============================================================
  const apiClient = new ClawMeetingApiClient(pluginConfig.serverUrl);

  const savedCreds = loadCredentials();
  if (savedCreds?.token) {
    apiClient.setToken(savedCreds.token);
    console.log(`[CM:init] 已恢复身份凭证: email=${savedCreds.email}, user_id=${savedCreds.user_id}, token=${savedCreds.token?.substring(0, 12)}...`);
  } else {
    console.log(`[CM:init] 无已保存的身份凭证`);
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
  const gatewayToken = api.config?.gateway?.auth?.token
    ?? process.env.OPENCLAW_GATEWAY_TOKEN
    ?? null;

  if (gatewayToken) {
    console.log(`[CM:init] gateway: port=${gatewayPort}, token=${gatewayToken.substring(0, 12)}... → 主动推送可用`);
  } else {
    console.log(`[CM:init] gateway: port=${gatewayPort}, token=NULL → 主动推送不可用，通知将在用户下次交互时展示`);
  }

  // ============================================================
  // 5. 主动推送（sessions_send 到所有 session，agent 在每个 session 独立处理）
  // ============================================================

  /**
   * 通过 sessions_send 发送到指定 session，触发 agent turn
   */
  async function sendViaSessionsSend(message: string, sessionKey?: string): Promise<{ ok: boolean; reply?: string }> {
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
          return { ok: false };
        }

        // 提取 agent reply：response 结构为 { ok, result: { details: { reply, status, ... } } }
        let reply: string | undefined;
        try {
          const json = JSON.parse(body);
          reply = json?.result?.details?.reply ?? undefined;
        } catch (_e) {
          // body 不是 JSON 或结构不同，reply 保持 undefined
        }

        console.log(`[CM:push] <<< sessions_send 成功 → ${sk} (${elapsed}ms) reply=${reply ? `"${reply.substring(0, 150)}..."` : "无"}`);
        return { ok: true, reply };
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

  /** 给主 session agent 的通知（带类型标记 + 指令，agent 看到后完整转告用户） */
  function buildAgentNotification(t: any): string {
    const msg = t.message ?? "";
    return [
      `[ClawMeeting ${t.task_type}]`,
      msg,
      "",
      "请将以上会议通知的完整信息转告用户，包括所有细节（会议名称、时间、时长、会议链接等），不要省略任何信息。",
    ].join("\n");
  }

  /** 给用户直接看的通知（不含 agent 指令，用于 message tool 直推到 Telegram 等渠道） */
  function buildDirectNotification(t: any): string {
    const title = t.title ?? "未知会议";
    const msg = t.message ?? "";
    const taskType = t.task_type;

    if (taskType === "MEETING_CONFIRMED") {
      return [`✅ 会议确认：「${title}」`, msg].filter(Boolean).join("\n");
    }
    if (taskType === "MEETING_OVER") {
      return [`❌ 会议取消：「${title}」`, msg].filter(Boolean).join("\n");
    }
    if (taskType === "COUNTER_PROPOSAL") {
      return [`🔄 会议协商：「${title}」需要你决策`, msg, "", "请在对话中回复你的决策（接受/提出新时段/拒绝）"].filter(Boolean).join("\n");
    }
    if (taskType === "MEETING_FAILED") {
      return [`❌ 会议协商失败：「${title}」`, msg, "", "请在对话中回复你的决策（取消/调整参数重试）"].filter(Boolean).join("\n");
    }
    return [`📅 [${taskType}] ${title}`, msg].filter(Boolean).join("\n");
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

      // ---- INITIAL_SUBMIT：agent 静默处理 ----
      if (taskType === "INITIAL_SUBMIT") {
        if (submittedMeetings.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 submittedMeetings`); continue; }
        if (pendingDecisions.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 pendingDecisions`); continue; }
        // 检查是否已在队列中
        if (taskQueue.some(q => q.task.meeting_id === meetingId)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }

        submittedMeetings.add(meetingId);

        const notifyLines = [
          `[ClawMeeting Meeting Invitation]`,
          `Meeting: "${title}"`,
          `Meeting ID: ${meetingId}`,
          `Organizer: ${t.initiator ?? "unknown"}`,
          `Duration: ${t.duration_minutes ?? "unknown"} minutes`,
        ];
        if (t.message) notifyLines.push("", t.message);

        // 拉取详情补充发起人的已提交时段
        try {
          const detail = await apiClient.getMeetingDetail(meetingId);
          const submitted = detail.participants.filter(
            (p: any) => p.has_submitted && p.latest_slots?.length > 0,
          );
          if (submitted.length > 0) {
            notifyLines.push("", "Submitted available slots:");
            for (const p of submitted) {
              notifyLines.push(`  ${p.email} (${p.role}): ${p.latest_slots.join(", ")}`);
            }
          }
        } catch (_e) { /* ignore */ }

        taskQueue.push({
          task: t,
          retryCount: 0,
          enqueuedAt: Date.now(),
          agentMsg: notifyLines.join("\n"),
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

      // ---- CONFIRMED / OVER 等：纯通知 ----
      if (notifiedMeetings.has(meetingId)) { console.log(`[CM:collect]   → 跳过: 已在 notifiedMeetings`); continue; }
      if (taskQueue.some(q => q.task.meeting_id === meetingId)) { console.log(`[CM:collect]   → 跳过: 已在队列中`); continue; }
      notifiedMeetings.add(meetingId);

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

    // 逐条处理（取第一条，处理完再取下一条）
    while (taskQueue.length > 0) {
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
          for (const [chName, chCtx] of extraChannels) {
            const target = parseChannelTarget(chCtx.sessionKey);
            if (target) {
              await sendViaMessageTool(target.channel, target.target, offlineMsg);
            }
          }
          pendingNotifications.push(offlineMsg);
        }

        notifiedMeetings.add(meetingId);
        saveNotifiedMeetings([...notifiedMeetings]);
        continue;
      }

      // ---- 正常处理：sessions_send → 提取 reply → message tool 分发 ----
      console.log(`[CM:queue] 处理: ${taskType}(${meetingId?.slice(-8)}) 「${title}」 retry=${item.retryCount} age=${Math.round(ageMs / 1000)}s`);

      const { ok: mainOk, reply } = await sendViaSessionsSend(item.agentMsg);

      if (!mainOk) {
        item.retryCount++;
        if (item.retryCount >= MAX_RETRY) {
          console.error(`[CM:queue] 超过最大重试次数(${MAX_RETRY})，fallback: ${taskType}(${meetingId?.slice(-8)})`);
          taskQueue.shift();
          // INITIAL_SUBMIT 是 agent 静默处理，失败时不推给用户（用户不需要知道这个中间状态）
          if (taskType === "INITIAL_SUBMIT") {
            console.log(`[CM:queue] INITIAL_SUBMIT 失败，不推送到用户渠道，等下次轮询重新入队`);
            submittedMeetings.delete(meetingId); // 允许下次轮询重新发现
          } else {
            // 其他类型推 directMsg 到用户
            pendingNotifications.push(item.directMsg);
            for (const [chName, chCtx] of extraChannels) {
              const target = parseChannelTarget(chCtx.sessionKey);
              if (target) {
                await sendViaMessageTool(target.channel, target.target, item.directMsg);
              }
            }
          }
        } else {
          console.log(`[CM:queue] sessions_send 失败，留在队列等下次重试 (retry=${item.retryCount}/${MAX_RETRY})`);
          break; // 不再处理后续任务，等下一轮
        }
        continue;
      }

      // sessions_send 成功，移出队列
      taskQueue.shift();
      console.log(`[CM:queue] sessions_send 成功`);

      // INITIAL_SUBMIT 是 agent 静默处理，不推到额外渠道（用户不需要看到中间状态）
      if (taskType === "INITIAL_SUBMIT") {
        console.log(`[CM:queue] INITIAL_SUBMIT 静默处理完成，不推送到额外渠道`);
      } else if (extraChannels.size > 0) {
        // 其他类型：提取 reply → message tool 分发到额外渠道
        const channelMsg = reply || item.directMsg;
        const source = reply ? "agent reply" : "directFallback";

        for (const [chName, chCtx] of extraChannels) {
          const target = parseChannelTarget(chCtx.sessionKey);
          if (target) {
            console.log(`[CM:queue] ${chName} 推送 (${source}): ${target.channel}:${target.target} (${channelMsg.length}字)`);
            await sendViaMessageTool(target.channel, target.target, channelMsg);
          }
        }
      }
    }

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
        // CONFIRMED/OVER：纯通知去重
        if (tt === "MEETING_CONFIRMED" || tt === "MEETING_OVER") {
          const dup = notifiedMeetings.has(mid);
          if (dup) console.log(`[CM:dedup] 去重跳过 ${tt}(${mid?.slice(-8)}) — 已在 notifiedMeetings`);
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
        // 其它未知类型：用 notifiedMeetings 去重，防止重复推送
        return !notifiedMeetings.has(mid);
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

  if (apiClient.getToken()) {
    console.log("[CM:init] 有已保存的 Token，立即启动轮询");
    pollingManager.start();
    startQueueProcessor();
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
  api.on?.(
    "gateway_start",
    () => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[CM:lifecycle] gateway_start: 启动轮询。");
        pollingManager.start();
        startQueueProcessor();
      }
    },
  );

  api.on?.(
    "gateway_stop",
    () => {
      pollingManager.stop();
      stopQueueProcessor();
      console.log("[CM:lifecycle] gateway_stop: 停止轮询。");
    },
  );

  // ============================================================
  // 14. 注册 4 个 Tools
  // ============================================================

  api.registerTool({
    ...bindIdentitySchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> bind_identity 调用: email=${params.email}`);
      const startMs = Date.now();
      const handler = createBindIdentityHandler(apiClient, () => {
        if (!pollingManager.isRunning()) {
          console.log(`[CM:tool] bind_identity → 绑定成功，启动轮询`);
          pollingManager.start();
          startQueueProcessor();
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
      const handler = createVerifyEmailCodeHandler(apiClient, () => {
        if (!pollingManager.isRunning()) {
          console.log(`[CM:tool] verify_email_code → 绑定成功，启动轮询`);
          pollingManager.start();
          startQueueProcessor();
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
      const handler = createInitiateMeetingHandler(apiClient);
      const result = await handler(params);
      console.log(`[CM:tool] <<< initiate_meeting 完成 (${Date.now() - startMs}ms): success=${(result as any).success}, meeting_id=${(result as any).meeting_id ?? "N/A"}`);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  const checkHandler = createCheckAndRespondTasksHandler(apiClient);
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
        if (pendingDecisions.has(params.meeting_id)) {
          pendingDecisions.delete(params.meeting_id);
          savePendingDecisions([...pendingDecisions]);
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} 用户已决策(${params.response_type})，从 pendingDecisions 移除`);
        }
        // 只在用户主动决策（非首次提交）时清除 submittedMeetings，允许新轮次重新自动提交
        // INITIAL 提交后不清除，否则会议仍在 COLLECTING 状态时轮询会重复推送
        if (params.response_type !== "INITIAL") {
          submittedMeetings.delete(params.meeting_id);
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} 从 submittedMeetings 移除（非 INITIAL）`);
        } else {
          console.log(`[CM:dedup] 会议 ${params.meeting_id.slice(-8)} INITIAL 提交成功，保留在 submittedMeetings 中`);
        }
      }

      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  const listHandler = createListMeetingsHandler(apiClient);
  api.registerTool({
    ...listMeetingsSchema,
    async execute(_id: string, params: any) {
      console.log(`[CM:tool] >>> list_meetings 调用: meeting_id=${params.meeting_id ?? "(列表模式)"}`);
      const startMs = Date.now();
      const result = await listHandler(params);
      console.log(`[CM:tool] <<< list_meetings 完成 (${Date.now() - startMs}ms): success=${(result as any).success}, total=${(result as any).total ?? "N/A"}`);
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
        const isWebchat = !channel || WEBCHAT_CHANNELS.has(channel);
        if (isWebchat && (sessionKey !== sessionCtx?.sessionKey || channel !== sessionCtx?.channel)) {
          const oldKey = sessionCtx.sessionKey;
          sessionCtx = { sessionKey, channel };
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
            "- On [ClawMeeting COUNTER_PROPOSAL]:",
            "  这是协商建议通知。完整展示协调方的建议内容，并询问用户：",
            "  1. 接受 → call check_and_respond_tasks with response_type='ACCEPT_PROPOSAL'",
            "  2. 提出新时段 → 用户提供时段，call response_type='NEW_PROPOSAL' + available_slots",
            "  3. 拒绝 → call response_type='REJECT'",
            "- On [ClawMeeting MEETING_FAILED]:",
            "  这是协商失败通知。完整展示失败原因，并询问用户：",
            "  1. 取消会议 → call check_and_respond_tasks with response_type='REJECT'",
            "  2. 调整时间重试 → 用户提供新时段，call response_type='NEW_PROPOSAL' + available_slots",
            "- On [ClawMeeting MEETING_CONFIRMED]:",
            "  这是会议确认通知。请将通知中的所有信息完整展示给用户：",
            "  会议名称、确认时间、时长、会议链接（如有）。不要省略任何细节，不要简化。",
            "- On [ClawMeeting MEETING_OVER]:",
            "  这是会议取消通知。告知用户会议已被取消，展示会议名称和原因。",
          ].join("\n")
        : [
            "[ClawMeeting Assistant - Setup Required]",
            "The user has not bound their identity yet. Guide them to provide their email to complete setup.",
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
    },
    { priority: 5 },
  );

  const channelList = extraChannels.size > 0 ? [...extraChannels.keys()].join(",") : "无";
  console.log(`[CM:init] ClawMeeting 插件加载完成。session=${sessionCtx.sessionKey}, 额外渠道=[${channelList}], polling=${pollingManager.isRunning() ? "运行中" : "未启动"}, gateway=${gatewayToken ? "可用" : "不可用"}`);
}
