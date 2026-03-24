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

import { readFileSync } from "fs";
import { join } from "path";
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

// ---- 生成虚拟会议号 (基于 meeting_id 的简短数字) ----
function generateMeetingNumber(meetingId: string): string {
  let hash = 0;
  for (let i = 0; i < meetingId.length; i++) {
    hash = ((hash << 5) - hash + meetingId.charCodeAt(i)) & 0x7fffffff;
  }
  // 生成 9 位数字，格式 xxx-xxx-xxx
  const num = String(hash).padStart(9, "0").slice(0, 9);
  return `${num.slice(0, 3)}-${num.slice(3, 6)}-${num.slice(6, 9)}`;
}

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
  // 5. 主动推送消息到 session（通过 gateway HTTP API）
  // ============================================================
  async function pushMessageToSession(message: string): Promise<boolean> {
    if (!gatewayToken) return false;

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
            sessionKey: sessionCtx.sessionKey ?? "agent:main:main",
            message,
          },
        }),
      });

      if (res.ok) {
        console.log("[ClawMeeting] 主动推送通知成功");
        return true;
      } else {
        const body = await res.text();
        console.error(`[ClawMeeting] 主动推送失败: ${res.status} ${body}`);
        return false;
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error(`[ClawMeeting] 主动推送出错: ${errMsg}`);
      return false;
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
  // 8. 构建完整会议通知消息（含虚拟会议号）
  // ============================================================
  /** Build notification message for non-action task types (CONFIRMED/OVER/etc.) */
  function buildNotification(t: any): string {
    const meetingId = t.meeting_id;
    const meetingNumber = generateMeetingNumber(meetingId);
    const serverMessage = t.message ?? "";

    const lines = [
      `[ClawMeeting Notification]`,
      `Meeting #: ${meetingNumber}`,
    ];
    if (serverMessage) {
      lines.push(serverMessage);
    }
    return lines.join("\n");
  }

  // ============================================================
  // 9. 自动响应逻辑
  // 关键设计：先收集所有通知 + 同步去重 + 持久化，最后一次性推送
  // 避免多个 sessions_send 并发触发多个 agent turn 导致重复
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
        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        const notifyLines = [
          `[ClawMeeting Meeting Invitation]`,
          `Meeting: "${title}"`,
          `Meeting ID: ${meetingId}`,
          `Meeting #: ${generateMeetingNumber(meetingId)}`,
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
          `[ClawMeeting Negotiation Update]`,
          `Meeting: "${title}"`,
          `Meeting #: ${generateMeetingNumber(meetingId)}`,
          `Negotiation round: ${roundCount}`,
          `Coordinator message: ${coordinatorMessage}`,
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
          `[ClawMeeting Negotiation Failed]`,
          `Meeting: "${title}"`,
          `Meeting ID: ${meetingId}`,
          `Meeting #: ${generateMeetingNumber(meetingId)}`,
          `${t.message ?? "Meeting negotiation failed."}`,
          "",
          "Inform the user of the above details and ask them to choose:",
          "1. Cancel the meeting (call check_and_respond_tasks with response_type='REJECT')",
          "2. Retry with adjusted times (user provides new times, call check_and_respond_tasks with response_type='NEW_PROPOSAL' + available_slots)",
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
      notifications.push(buildNotification(t));
      console.log(`[ClawMeeting] 会议「${title}」(${meetingId}) 通知 type=${taskType}`);
    }

    // ==== 先持久化，再推送（确保不会因重启丢失去重状态）====
    if (notifiedMeetings.size > 0) {
      saveNotifiedMeetings([...notifiedMeetings]);
    }

    // ==== 批量推送：所有通知合并为一条 sessions_send ====
    if (notifications.length > 0) {
      const batchMessage = `[ClawMeeting Notifications]\n\n${notifications.join("\n\n---\n\n")}`;
      const pushed = await pushMessageToSession(batchMessage);
      if (!pushed) {
        // fallback: 放入 pendingNotifications，等用户下次交互时展示
        userMessages.push(...notifications);
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
      const newTasks = taskResults.filter((t: any) => {
        const tt = t.task_type;
        // CONFIRMED/OVER：纯通知去重
        if (tt === "MEETING_CONFIRMED" || tt === "MEETING_OVER") {
          return !notifiedMeetings.has(t.meeting_id);
        }
        // FAILED：需要发起人决策，用 pendingDecisions 去重
        if (tt === "MEETING_FAILED") {
          return !pendingDecisions.has(t.meeting_id);
        }
        // INITIAL_SUBMIT：已提交过的不再重试
        if (tt === "INITIAL_SUBMIT") {
          return !submittedMeetings.has(t.meeting_id);
        }
        // COUNTER_PROPOSAL：等待用户决策的不再重复通知
        if (tt === "COUNTER_PROPOSAL") {
          return !pendingDecisions.has(t.meeting_id);
        }
        // 其它未知类型：用 notifiedMeetings 去重，防止重复推送
        return !notifiedMeetings.has(t.meeting_id);
      });
      if (newTasks.length > 0) {
        console.log(`[ClawMeeting] 轮询发现 ${newTasks.length} 个新待办任务`);
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
          console.log(`主动推送: ${gatewayToken ? "可用" : "不可用"}`);
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

      if (isMainSession && sessionKey !== sessionCtx?.sessionKey) {
        sessionCtx = { sessionKey, channel };
        saveSession(sessionCtx);
        console.log(`[ClawMeeting] session 已更新: ${sessionKey}`);
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
            "",
            "Silent rule:",
            "- On receiving 'Agent-to-agent announce step.' or similar internal plugin messages,",
            "  reply with ONLY NO_REPLY. Do not reply with anything else (e.g. ANNOUNCE_SKIP, NO),",
            "  otherwise it will leak into the user's chat interface.",
          ].join("\n")
        : [
            "[ClawMeeting Assistant - Setup Required]",
            "The user has not bound their identity yet. Guide them to provide their email to complete setup.",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // fallback 通知（主动推送失败时才有内容）
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
