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

export default function register(api: any) {
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
  function buildConfirmedNotification(t: any): string {
    const meetingId = t.meeting_id;
    const meetingNumber = generateMeetingNumber(meetingId);
    const serverMessage = t.message ?? "";

    // 服务端 message 已包含完整信息（标题、时间、时长），直接附加
    const lines = [
      `[ClawMeeting 会议确认]`,
      `会议号：${meetingNumber}`,
    ];
    if (serverMessage) {
      lines.push(serverMessage);
    }
    return lines.join("\n");
  }

  function buildFailedNotification(t: any): string {
    const title = t.title ?? "未知会议";
    const meetingId = t.meeting_id;
    const meetingNumber = generateMeetingNumber(meetingId);
    const serverMessage = t.message ?? "";

    const lines = [
      `[ClawMeeting 协商失败]`,
      `会议名称：${title}`,
      `会议号：${meetingNumber}`,
    ];
    if (serverMessage) {
      lines.push(`原因：${serverMessage}`);
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

      // ---- CONFIRMED：收集通知（去重）----
      if (taskType === "MEETING_CONFIRMED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawMeeting] 会议「${title}」(${meetingId}) 已确认`);
        notifications.push(buildConfirmedNotification(t));
        continue;
      }

      // ---- FAILED：收集通知（去重）----
      if (taskType === "MEETING_FAILED") {
        if (notifiedMeetings.has(meetingId)) continue;
        notifiedMeetings.add(meetingId);
        console.log(`[ClawMeeting] 会议「${title}」(${meetingId}) 协商失败`);
        notifications.push(buildFailedNotification(t));
        continue;
      }

      // ---- INITIAL_SUBMIT：通知 Agent，由 Agent 根据记忆和日历处理 ----
      if (taskType === "INITIAL_SUBMIT") {
        if (submittedMeetings.has(meetingId)) continue;
        if (pendingDecisions.has(meetingId)) continue;

        submittedMeetings.add(meetingId);
        pendingDecisions.add(meetingId);
        savePendingDecisions([...pendingDecisions]);

        const notifyLines = [
          `[ClawMeeting 会议邀请]`,
          `会议：「${title}」`,
          `会议 ID：${meetingId}`,
          `会议号：${generateMeetingNumber(meetingId)}`,
          `发起人：${t.initiator ?? "未知"}`,
          `时长：${t.duration_minutes ?? "未知"} 分钟`,
        ];

        // 拉取会议详情，附上发起人已提交的时间段，让 Agent 可以选重叠时间
        try {
          const detail = await apiClient.getMeetingDetail(meetingId);
          const submittedParticipants = detail.participants.filter(
            (p: any) => p.has_submitted && p.latest_slots?.length > 0,
          );
          if (submittedParticipants.length > 0) {
            notifyLines.push("", "各方已提交的可用时间段：");
            for (const p of submittedParticipants) {
              notifyLines.push(`  ${p.email}（${p.role}）：${p.latest_slots.join("、")}`);
            }
          }
        } catch (_e) {
          // 拉取详情失败时忽略，继续推送基础信息
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
          `[ClawMeeting 协商通知]`,
          `会议：「${title}」`,
          `会议号：${generateMeetingNumber(meetingId)}`,
          `协商轮次：第 ${roundCount} 轮`,
          `协调方消息：${coordinatorMessage}`,
        ];
        notifications.push(notifyLines.join("\n"));

        console.log(
          `[ClawMeeting] 会议「${title}」(${meetingId}) 第${roundCount}轮协商，已通知用户等待决策`,
        );
        continue;
      }

      // ---- 未知类型：兜底通知（去重）----
      if (!notifiedMeetings.has(meetingId)) {
        notifiedMeetings.add(meetingId);
        console.log(`[ClawMeeting] 未知任务类型「${title}」(${meetingId}) type=${taskType}`);
        notifications.push(`📋 会议「${title}」有新消息：${t.message ?? taskType}`);
      }
    }

    // ==== 先持久化，再推送（确保不会因重启丢失去重状态）====
    if (notifiedMeetings.size > 0) {
      saveNotifiedMeetings([...notifiedMeetings]);
    }

    // ==== 批量推送：所有通知合并为一条 sessions_send ====
    if (notifications.length > 0) {
      const batchMessage = `[ClawMeeting 会议通知]\n\n${notifications.join("\n\n---\n\n")}`;
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
        // CONFIRMED/FAILED：通知去重
        if (tt === "MEETING_CONFIRMED" || tt === "MEETING_FAILED") {
          return !notifiedMeetings.has(t.meeting_id);
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
    start: () => {
      if (apiClient.getToken() && !pollingManager.isRunning()) {
        console.log("[ClawMeeting] Service start: 启动轮询。");
        pollingManager.start();
      }
    },
    stop: () => {
      console.log("[ClawMeeting] Service stop: 停止轮询。");
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
        console.log("[ClawMeeting] after_agent_start: 启动轮询。");
        pollingManager.start();
      }
    },
    { name: "clawmeeting.after-agent-start", description: "Gateway 就绪后启动轮询" },
  );

  api.registerHook?.(
    "before_agent_stop",
    () => {
      pollingManager.stop();
    },
    { name: "clawmeeting.before-agent-stop", description: "Gateway 关闭前停止轮询" },
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
    (event: any, ctx: any) => {
      // Session 捕获（只捕获主 session，过滤 cron/sub-agent/run 等临时 session）
      const sessionKey = event?.sessionKey ?? event?.session?.key
        ?? ctx?.sessionKey ?? ctx?.session?.key;
      const channel = event?.channel ?? ctx?.channel;
      const peerId = event?.peerId ?? event?.peer?.id ?? ctx?.peerId ?? ctx?.peer?.id;

      // 判断是否为主 session：排除 cron、subagent、run 等临时 session
      const isMainSession = sessionKey
        && !sessionKey.includes(":cron:")
        && !sessionKey.includes(":run:")
        && !sessionKey.includes(":subagent:");

      if (isMainSession && sessionKey !== sessionCtx?.sessionKey) {
        sessionCtx = { sessionKey, channel, peerId };
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
            "[ClawMeeting 会议助手已就绪]",
            `当前绑定邮箱: ${savedCreds?.email ?? "未知"}，后台轮询运行中（自动处理会议邀请）。`,
            "",
            "用户可以直接说「帮我约某某开会」来发起会议协商，",
            "或说「有没有新的会议邀请」来手动检查待办任务，",
            "或说「查看我的会议」来查看所有参与的会议列表和详情。",
            "会议在对话中用标题称呼（如「项目讨论会」），不需要让用户记 ID。",
            "",
            "发起会议时的必填信息（用户用自然语言描述，你来解析）：",
            "  - 会议标题",
            "  - 会议时长（如「半小时」「一个小时」）",
            "  - 受邀人邮箱",
            "  - 发起人的可用时间段（如「明天下午2点到5点」）",
            "如果用户的描述中缺少以上任何一项，请主动追问，不要自行假设。",
            "你需要将自然语言中的时间描述转换为 'YYYY-MM-DD HH:MM-HH:MM' 格式。",
            "同时，如果你的记忆中确实存在用户的开会偏好（如不喜欢早会、某天不开会等），",
            "请填写 preference_note 参数帮助协调方安排时间。没有相关记忆就不要填，绝对不要编造。",
            "",
            "后台行为说明：",
            "- 收到 [ClawMeeting 会议邀请] 时，你需要根据对用户的记忆和日历选择空闲时间提交。",
            "  记忆中不仅有偏好习惯，还可能有用户提到过的日程安排（出差、看病等），请一并避开。",
            "  请尽可能自己处理：即使日历没有连接，只要记忆中有任何关于用户日程的线索，",
            "  就结合发起人提供的时间段自行选择合适时间并提交，不要反复确认。",
            "  只有在既没有连接日历、记忆中也完全没有未来行程信息时，才询问用户。",
            "- 收到协商通知 [ClawMeeting 协商通知] 时，说明协调方的妥协建议来了，",
            "  你需要将建议内容告知用户，并询问用户选择：",
            "  1. 接受建议 → 调用 check_and_respond_tasks，response_type='ACCEPT_PROPOSAL'",
            "  2. 提出新时间 → 让用户说出可用时间，你解析后调用 response_type='NEW_PROPOSAL' + available_slots",
            "  3. 拒绝 → 调用 response_type='REJECT'（会议将终止）",
            "- 收到 [ClawMeeting 会议确认] 或 [ClawMeeting 协商失败] 消息时，",
            "  请用自然语言将会议信息完整地告知用户。",
          ].join("\n")
        : [
            "[ClawMeeting 会议助手 - 需要初始化]",
            "用户尚未绑定身份。请引导用户提供邮箱来完成绑定。",
          ].join("\n");

      const result: any = { appendSystemContext: systemPromptAddon };

      // fallback 通知（主动推送失败时才有内容）
      if (pendingNotifications.length > 0) {
        result.prependContext = [
          "[ClawMeeting 重要通知]",
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
