// ============================================================
// ClawSync Plugin - 入口文件
// OpenClaw 插件注册点：注册 3 个核心 Tools + 轮询服务
//
// 架构设计：
//   1. 插件加载时：恢复 Token → 有 Token 则立即启动轮询
//   2. registerService：管理轮询生命周期（start/stop）
//   3. before_prompt_build：捕获 session + 注入 system prompt
//   4. 轮询发现任务 → 插件自动读日历提交 → 仅在确认/失败时通知用户
//   5. 各状态处理：
//      COLLECTING    → 自动读日历提交空闲时间
//      ANALYZING     → 跳过（等服务端分析完）
//      NEGOTIATING   → 自动重新提交，处理不了才问用户
//      CONFIRMED     → 通知用户会议已确认（仅一次）
//      FAILED        → 通知用户协商失败（仅一次）
// ============================================================

import { readFileSync } from "fs";
import { join } from "path";
import { ClawSyncApiClient } from "./src/utils/api-client.js";
import {
  initStorage,
  loadCredentials,
  saveSession,
  loadSession,
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
  // 4. 通知去重：记住已通知过的 meeting_id，避免重复推送
  // ============================================================
  const notifiedMeetings = new Set<string>();

  // ============================================================
  // 5. 待推送通知队列（仅限重要事件：会议确认 / 协商失败）
  // ============================================================
  let pendingNotifications: string[] = [];

  // ============================================================
  // 6. 自动响应逻辑
  //    - INITIAL_SUBMIT / COUNTER_PROPOSAL → 读日历自动提交
  //    - MEETING_CONFIRMED / MEETING_FAILED → 通知用户（仅一次）
  //    - 未知类型 → 兜底通知
  //    返回值：需要通知用户的消息列表
  // ============================================================
  async function autoRespondToTasks(tasks: unknown[]): Promise<string[]> {
    const userMessages: string[] = [];
    // 获取日历空闲时段（{start, end} 对象格式）
    const calendarSlots = getMockAvailableSlots();

    for (const task of tasks) {
      const t = task as any;
      const meetingId = t.meeting_id;
      const title = t.title ?? "未知会议";
      const taskType = t.task_type;

      // ---- CONFIRMED：通知用户（去重）----
      if (taskType === "MEETING_CONFIRMED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawSync] 会议「${title}」(${meetingId}) 已确认，通知用户`);
        userMessages.push(`✅ 会议「${title}」已确认！${t.message ?? ""}`);
        continue;
      }

      // ---- FAILED：通知用户（去重）----
      if (taskType === "MEETING_FAILED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawSync] 会议「${title}」(${meetingId}) 协商失败，通知用户`);
        userMessages.push(`⚠️ 会议「${title}」协商失败。${t.message ?? ""}`);
        continue;
      }

      // ---- INITIAL_SUBMIT / COUNTER_PROPOSAL：自动读日历提交 ----
      if (taskType === "INITIAL_SUBMIT" || taskType === "COUNTER_PROPOSAL") {
        const responseType = taskType === "INITIAL_SUBMIT" ? "INITIAL" : "COUNTER";

        try {
          const result = await apiClient.submitAvailability(meetingId, {
            response_type: responseType,
            available_slots: calendarSlots, // 直接传 {start, end} 对象数组
          });

          console.log(
            `[ClawSync] 自动提交「${title}」(${meetingId}) → ${responseType}, status=${result.status}`,
          );

          // 提交后即时返回 CONFIRMED
          if (result.coordinator_result?.status === "CONFIRMED" && result.coordinator_result?.final_time) {
            if (!notifiedMeetings.has(meetingId)) {
              notifiedMeetings.add(meetingId);
              userMessages.push(
                `✅ 会议「${title}」已确认！时间：${result.coordinator_result.final_time}`,
              );
            }
          }

          // 提交后即时返回 FAILED
          if (result.coordinator_result?.status === "FAILED" || result.coordinator_result?.status === "NO_MATCH") {
            if (!notifiedMeetings.has(meetingId)) {
              notifiedMeetings.add(meetingId);
              userMessages.push(
                `⚠️ 会议「${title}」协商未能达成一致。` +
                (result.coordinator_result?.reasoning ? `原因：${result.coordinator_result.reasoning}` : ""),
              );
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
        userMessages.push(`📋 会议「${title}」有新消息：${t.message ?? taskType}`);
      }
    }

    return userMessages;
  }

  // ============================================================
  // 7. 轮询管理器 — 自动处理，不唤醒 Agent
  // ============================================================
  const taskHandler = createCheckAndRespondTasksHandler(apiClient);
  const pollingManager = new PollingManager({
    intervalMs: pluginConfig.pollingIntervalMs,
    enabled: pluginConfig.autoRespond,
    onPoll: async () => {
      const result = await taskHandler({});
      if ((result as any).pending_count > 0) {
        console.log(`[ClawSync] 轮询发现 ${(result as any).pending_count} 个待办任务`);
      }
      return result;
    },
    onAutoRespond: autoRespondToTasks,
    onNotifyUser: (messages: string[]) => {
      // 仅将重要通知加入队列（会议确认 / 协商失败）
      pendingNotifications.push(...messages);
    },
  });

  // ============================================================
  // 8. 插件加载时：有 Token 立即启动轮询
  // ============================================================
  if (apiClient.getToken()) {
    console.log("[ClawSync] 有已保存的 Token，立即启动轮询。");
    pollingManager.start();
  }

  // ============================================================
  // 9. registerService：管理轮询生命周期
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
  // 10. 生命周期钩子
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
  // 11. 注册 3 个 Tools
  // ============================================================

  // Tool 1: bind_identity
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

  // Tool 2: initiate_meeting
  api.registerTool({
    ...initiateMeetingSchema,
    async execute(_id: string, params: any) {
      const handler = createInitiateMeetingHandler(apiClient);
      const result = await handler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  // Tool 3: check_and_respond_tasks（用户主动查询时使用）
  const checkHandler = createCheckAndRespondTasksHandler(apiClient);
  api.registerTool({
    ...checkAndRespondTasksSchema,
    async execute(_id: string, params: any) {
      const result = await checkHandler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  // ============================================================
  // 12. 注册 CLI 命令
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
          if (creds?.email) {
            console.log(`已绑定邮箱: ${creds.email}`);
            console.log(`用户 ID: ${creds.user_id}`);
          } else {
            console.log("身份状态: 未绑定");
          }
          if (session?.sessionKey) {
            console.log(`Session: ${session.sessionKey}`);
          } else {
            console.log("Session: 使用默认值 agent:main:main");
          }
        });
    },
    { commands: ["clawsync-status"] },
  );

  // ============================================================
  // 13. before_prompt_build
  //     职责：
  //     a) 从 event 中捕获/更新 session
  //     b) 注入 system prompt（引导绑定 或 功能说明）
  //     c) 仅在有重要通知时才推送（会议确认 / 协商失败）
  // ============================================================
  api.on?.(
    "before_prompt_build",
    (event: any, ctx: any) => {
      // --- a) Session 捕获 ---
      const sessionKey = event?.sessionKey ?? event?.session?.key
        ?? ctx?.sessionKey ?? ctx?.session?.key;
      const channel = event?.channel ?? ctx?.channel;
      const peerId = event?.peerId ?? event?.peer?.id ?? ctx?.peerId ?? ctx?.peer?.id;
      if (sessionKey && sessionKey !== sessionCtx?.sessionKey) {
        sessionCtx = { sessionKey, channel, peerId };
        saveSession(sessionCtx);
        console.log(`[ClawSync] session 已更新: ${sessionKey}`);
      }

      // --- b) System prompt 注入 ---
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
          ].join("\n")
        : [
            "[ClawSync 会议助手 - 需要初始化]",
            "用户尚未绑定身份。当用户首次与你对话时，请友好地引导用户提供邮箱来完成绑定。",
            "你可以说：「我注意到你还没有激活会议助手 ClawSync。它可以帮你一句话约会议、自动处理邀请。",
            "要开始使用，请告诉我你的邮箱地址，我来帮你注册。」",
            "",
            "绑定成功后用户可以：",
            "- 一句话发起会议（如「帮我约 Bob 明天开会」）",
            "- 自动处理收到的会议邀请（后台静默完成）",
            "- 收到妥协建议时在这里做决定",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // --- c) 仅推送重要通知（会议确认 / 协商失败） ---
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
