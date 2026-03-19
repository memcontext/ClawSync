// ============================================================
// ClawSync Plugin - 入口文件
// 架构设计：
//   1. 插件加载时：恢复 Token → 有 Token 则立即启动轮询
//   2. 各状态处理：
//      COLLECTING    → 自动读日历提交空闲时间
//      ANALYZING     → 跳过（等服务端分析完）
//      NEGOTIATING   → 自动重新提交
//      CONFIRMED     → 主动推送通知给用户（含完整会议信息 + 虚拟会议号）
//      FAILED        → 主动推送通知给用户
//   3. 通知去重：持久化已通知 meeting_id
//   4. 主动推送：通过 gateway HTTP API 的 sessions_send 触发 agent 回合
// ============================================================

import { readFileSync } from "fs";
import { join } from "path";
import { ClawSyncApiClient } from "./src/utils/api-client.js";
import {
  initStorage,
  loadCredentials,
  saveSession,
  loadSession,
  loadNotifiedMeetings,
  saveNotifiedMeetings,
} from "./src/utils/storage.js";
import { PollingManager } from "./src/utils/polling-manager.js";
import {
  getMockAvailableSlots,
  getMockAvailableSlotsAsStrings,
} from "./src/utils/mock-calendar.js";

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

// Types
import type { ClawSyncPluginConfig, SessionContext } from "./src/types/index.js";

// ---- 默认配置 ----
const DEFAULT_CONFIG: ClawSyncPluginConfig = {
  serverUrl: "http://192.168.22.28:8000",
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
    return manifest.id ?? "clawsync";
  } catch {
    return "clawsync";
  }
}

export default function register(api: any) {
  const PLUGIN_ID = readPluginId();

  // ============================================================
  // 1. 读取插件配置
  // ============================================================
  const pluginConfig: ClawSyncPluginConfig = {
    ...DEFAULT_CONFIG,
    ...(api.config?.plugins?.entries?.[PLUGIN_ID]?.config ?? {}),
  };
  console.log(`[${PLUGIN_ID}] 插件配置: serverUrl=${pluginConfig.serverUrl}`);

  // 初始化存储目录
  initStorage(PLUGIN_ID);

  // ============================================================
  // 2. 初始化 API Client + 恢复 Token
  // ============================================================
  const apiClient = new ClawSyncApiClient(pluginConfig.serverUrl);

  const savedCreds = loadCredentials();
  if (savedCreds?.token) {
    apiClient.setToken(savedCreds.token);
    console.log(`[ClawSync] 已恢复身份凭证: ${savedCreds.email} (user_id: ${savedCreds.user_id})`);
  }

  // ============================================================
  // 3. Session 管理
  // ============================================================
  let sessionCtx: SessionContext = loadSession() ?? { sessionKey: "agent:main:main" };
  if (sessionCtx.sessionKey) {
    console.log(`[ClawSync] session: ${sessionCtx.sessionKey}`);
  }

  // ============================================================
  // 4. Gateway 认证 Token（用于主动推送消息）
  // ============================================================
  const gatewayPort = api.config?.gateway?.port ?? 18789;
  const gatewayToken = api.config?.gateway?.auth?.token
    ?? process.env.OPENCLAW_GATEWAY_TOKEN
    ?? null;

  if (gatewayToken) {
    console.log("[ClawSync] 已获取 gateway token，支持主动推送通知");
  } else {
    console.log("[ClawSync] 未获取 gateway token，通知将在用户下次交互时展示");
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
        console.log("[ClawSync] 主动推送通知成功");
        return true;
      } else {
        const body = await res.text();
        console.error(`[ClawSync] 主动推送失败: ${res.status} ${body}`);
        return false;
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error(`[ClawSync] 主动推送出错: ${errMsg}`);
      return false;
    }
  }

  // ============================================================
  // 6. 通知去重：从文件恢复已通知的 meeting_id
  // ============================================================
  const notifiedMeetings = new Set<string>(loadNotifiedMeetings());
  if (notifiedMeetings.size > 0) {
    console.log(`[ClawSync] 已恢复 ${notifiedMeetings.size} 个已通知会议记录`);
  }

  // ============================================================
  // 7. 待推送通知队列（备用：主动推送失败时 fallback 到 prependContext）
  // ============================================================
  let pendingNotifications: string[] = [];

  // ============================================================
  // 8. 构建完整会议通知消息（含虚拟会议号）
  // ============================================================
  function buildConfirmedNotification(t: any): string {
    const title = t.title ?? "未知会议";
    const meetingId = t.meeting_id;
    const meetingNumber = generateMeetingNumber(meetingId);
    const serverMessage = t.message ?? "";

    // 从服务端消息中提取时间和时长信息
    const lines = [
      `📅 会议确认通知`,
      ``,
      `会议名称：${title}`,
      `会议号：${meetingNumber}`,
    ];

    // 如果服务端消息包含详细信息，直接附加
    if (serverMessage) {
      lines.push(`${serverMessage}`);
    }

    lines.push(``, `如需查看详情，请说「查看我的会议」。`);

    return lines.join("\n");
  }

  function buildFailedNotification(t: any): string {
    const title = t.title ?? "未知会议";
    const meetingId = t.meeting_id;
    const meetingNumber = generateMeetingNumber(meetingId);
    const serverMessage = t.message ?? "";

    const lines = [
      `⚠️ 会议协商失败通知`,
      ``,
      `会议名称：${title}`,
      `会议号：${meetingNumber}`,
    ];

    if (serverMessage) {
      lines.push(`原因：${serverMessage}`);
    }

    lines.push(``, `如需重新发起协商，请告诉我。`);

    return lines.join("\n");
  }

  // ============================================================
  // 9. 自动响应逻辑
  // ============================================================
  async function autoRespondToTasks(tasks: unknown[]): Promise<string[]> {
    const userMessages: string[] = [];
    const calendarSlots = getMockAvailableSlots();

    for (const task of tasks) {
      const t = task as any;
      const meetingId = t.meeting_id;
      const title = t.title ?? "未知会议";
      const taskType = t.task_type;

      // ---- CONFIRMED：主动推送通知（去重）----
      if (taskType === "MEETING_CONFIRMED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawSync] 会议「${title}」(${meetingId}) 已确认，推送通知`);

        const notification = buildConfirmedNotification(t);
        const pushed = await pushMessageToSession(
          `[ClawSync 会议确认] 请将以下会议确认信息通知用户：\n${notification}`
        );

        // 如果主动推送失败，fallback 到 prependContext
        if (!pushed) {
          userMessages.push(notification);
        }
        continue;
      }

      // ---- FAILED：主动推送通知（去重）----
      if (taskType === "MEETING_FAILED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawSync] 会议「${title}」(${meetingId}) 协商失败，推送通知`);

        const notification = buildFailedNotification(t);
        const pushed = await pushMessageToSession(
          `[ClawSync 协商失败] 请将以下协商失败信息通知用户：\n${notification}`
        );

        if (!pushed) {
          userMessages.push(notification);
        }
        continue;
      }

      // ---- INITIAL_SUBMIT / COUNTER_PROPOSAL：自动读日历提交 ----
      if (taskType === "INITIAL_SUBMIT" || taskType === "COUNTER_PROPOSAL") {
        const responseType = taskType === "INITIAL_SUBMIT" ? "INITIAL" : "COUNTER";

        try {
          const result = await apiClient.submitAvailability(meetingId, {
            response_type: responseType,
            available_slots: calendarSlots,
          });

          console.log(
            `[ClawSync] 自动提交「${title}」(${meetingId}) → ${responseType}, status=${result.status}`,
          );

          // 提交后即时返回 CONFIRMED
          if (result.coordinator_result?.status === "CONFIRMED" && result.coordinator_result?.final_time) {
            if (!notifiedMeetings.has(meetingId)) {
              notifiedMeetings.add(meetingId);
              const notification = buildConfirmedNotification({
                ...t,
                message: `时间：${result.coordinator_result.final_time}\n${result.coordinator_result.reasoning ?? ""}`,
              });
              const pushed = await pushMessageToSession(
                `[ClawSync 会议确认] 请将以下会议确认信息通知用户：\n${notification}`
              );
              if (!pushed) userMessages.push(notification);
            }
          }

          // 提交后即时返回 FAILED
          if (result.coordinator_result?.status === "FAILED" || result.coordinator_result?.status === "NO_MATCH") {
            if (!notifiedMeetings.has(meetingId)) {
              notifiedMeetings.add(meetingId);
              const notification = buildFailedNotification({
                ...t,
                message: result.coordinator_result?.reasoning ?? "超过最大协商轮数",
              });
              const pushed = await pushMessageToSession(
                `[ClawSync 协商失败] 请将以下协商失败信息通知用户：\n${notification}`
              );
              if (!pushed) userMessages.push(notification);
            }
          }
        } catch (err) {
          const errMsg = err instanceof Error ? err.message : String(err);
          console.error(`[ClawSync] 自动提交「${title}」失败: ${errMsg}`);
        }
        continue;
      }

      // ---- 未知类型：兜底通知（去重）----
      if (!notifiedMeetings.has(meetingId)) {
        notifiedMeetings.add(meetingId);
        console.log(`[ClawSync] 未知任务类型「${title}」(${meetingId}) type=${taskType}`);
        const msg = `📋 会议「${title}」有新消息：${t.message ?? taskType}`;
        const pushed = await pushMessageToSession(msg);
        if (!pushed) userMessages.push(msg);
      }
    }

    // 持久化已通知的会议列表
    if (notifiedMeetings.size > 0) {
      saveNotifiedMeetings([...notifiedMeetings]);
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
      // 过滤掉已通知过的任务
      const taskResults = (result as any).task_results ?? [];
      const newTasks = taskResults.filter(
        (t: any) => !notifiedMeetings.has(t.meeting_id),
      );
      if (newTasks.length > 0) {
        console.log(`[ClawSync] 轮询发现 ${newTasks.length} 个新待办任务`);
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
    console.log("[ClawSync] 有已保存的 Token，立即启动轮询。");
    pollingManager.start();
  }

  // ============================================================
  // 12. registerService
  // ============================================================
  api.registerService?.({
    id: "clawsync-polling",
    start: () => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[ClawSync] Service start: 启动轮询。");
        pollingManager.start();
      }
    },
    stop: () => {
      console.log("[ClawSync] Service stop: 停止轮询。");
      pollingManager.stop();
    },
  });

  // ============================================================
  // 13. 生命周期钩子
  // ============================================================
  api.registerHook?.(
    "after_agent_start",
    () => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[ClawSync] after_agent_start: 启动轮询。");
        pollingManager.start();
      }
    },
    { name: "clawsync.after-agent-start", description: "Gateway 就绪后启动轮询" },
  );

  api.registerHook?.(
    "before_agent_stop",
    () => {
      pollingManager.stop();
    },
    { name: "clawsync.before-agent-stop", description: "Gateway 关闭前停止轮询" },
  );

  // ============================================================
  // 14. 注册 3 个 Tools
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
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  // ============================================================
  // 15. CLI 命令
  // ============================================================
  api.registerCli?.(
    ({ program }: any) => {
      program
        .command("clawsync-status")
        .description("查看 ClawSync 插件状态")
        .action(() => {
          const creds = loadCredentials();
          const session = loadSession();
          console.log("=== ClawSync Meeting Negotiator ===");
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
    { commands: ["clawsync-status"] },
  );

  // ============================================================
  // 16. before_prompt_build
  // ============================================================
  api.on?.(
    "before_prompt_build",
    (event: any, ctx: any) => {
      // Session 捕获
      const sessionKey = event?.sessionKey ?? event?.session?.key
        ?? ctx?.sessionKey ?? ctx?.session?.key;
      const channel = event?.channel ?? ctx?.channel;
      const peerId = event?.peerId ?? event?.peer?.id ?? ctx?.peerId ?? ctx?.peer?.id;
      if (sessionKey && sessionKey !== sessionCtx?.sessionKey) {
        sessionCtx = { sessionKey, channel, peerId };
        saveSession(sessionCtx);
        console.log(`[ClawSync] session 已更新: ${sessionKey}`);
      }

      // System prompt 注入
      const creds = loadCredentials();
      const isBound = !!creds?.token;

      const systemPromptAddon = isBound
        ? [
            "[ClawSync 会议助手已就绪]",
            `当前绑定邮箱: ${creds!.email}，后台轮询运行中（自动处理会议邀请）。`,
            "用户可以直接说「帮我约某某开会」来发起会议协商，",
            "或说「有没有新的会议邀请」来手动检查待办任务。",
            "会议在对话中用标题称呼（如「项目讨论会」），不需要让用户记 ID。",
            "",
            "注意：后台轮询会自动读取日历并提交空闲时间，无需每次通知用户。",
            "只有在会议最终确认或协商无法达成一致时才通知用户。",
            "收到 [ClawSync 会议确认] 或 [ClawSync 协商失败] 消息时，",
            "请用自然语言将会议信息完整地告知用户，包括会议名称、会议号、时间等。",
          ].join("\n")
        : [
            "[ClawSync 会议助手 - 需要初始化]",
            "用户尚未绑定身份。请引导用户提供邮箱来完成绑定。",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // fallback 通知（主动推送失败时才有内容）
      if (pendingNotifications.length > 0) {
        result.prependContext = [
          "[ClawSync 重要通知]",
          ...pendingNotifications,
        ].join("\n");
        pendingNotifications = [];
      }

      return result;
    },
    { priority: 5 },
  );

  console.log("[ClawSync] ClawSync Meeting Negotiator 插件已加载。");
}
