// ============================================================
// ClawSync Plugin - 入口文件
// OpenClaw 插件注册点：注册 3 个核心 Tools + 轮询服务
//
// 架构设计：
//   1. 插件加载时：恢复 Token → 有 Token 则立即启动轮询
//   2. registerService：管理轮询生命周期（start/stop）
//   3. before_prompt_build：捕获 session + 注入 system prompt + 推送待办通知
//   4. 所有 API 均严格对齐 OpenClaw 插件文档 + API_REFERENCE.md
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
  //    - 从本地恢复，如无则使用默认值 "agent:main:main"
  //    - before_prompt_build 中动态更新
  // ============================================================
  let sessionCtx: SessionContext = loadSession() ?? { sessionKey: "agent:main:main" };
  if (sessionCtx.sessionKey) {
    console.log(`[ClawSync] session: ${sessionCtx.sessionKey}`);
  }

  // ============================================================
  // 4. 待推送通知队列
  //    轮询发现任务 → 存入队列 → before_prompt_build 时注入给 Agent
  // ============================================================
  let pendingNotifications: string[] = [];

  // ============================================================
  // 5. 轮询管理器
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
    onNeedAgentAction: (tasks: unknown[]) => {
      for (const task of tasks) {
        const t = task as any;
        const title = t.title ?? "未知会议";
        const taskType = t.task_type;

        if (taskType === "INITIAL_SUBMIT") {
          pendingNotifications.push(
            `[会议邀请] 「${title}」— ${t.server_message ?? "你收到了一个会议邀请，请提交空闲时间。"}`
          );
        } else if (taskType === "COUNTER_PROPOSAL") {
          pendingNotifications.push(
            `[协商通知] 「${title}」— ${t.coordinator_message ?? "协调方发来了新的协商建议。"}`
          );
        }
      }
    },
  });

  // ============================================================
  // 6. 插件加载时：有 Token 立即启动轮询（不等钩子）
  // ============================================================
  if (apiClient.getToken()) {
    console.log("[ClawSync] 有已保存的 Token，立即启动轮询。");
    pollingManager.start();
  }

  // ============================================================
  // 7. registerService：管理轮询生命周期（文档化 API）
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
  // 8. 生命周期钩子（补充触发，兼容正常冷启动顺序）
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
  // 9. 注册 3 个 Tools
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

  // Tool 3: check_and_respond_tasks
  const checkHandler = createCheckAndRespondTasksHandler(apiClient);
  api.registerTool({
    ...checkAndRespondTasksSchema,
    async execute(_id: string, params: any) {
      const result = await checkHandler(params);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  });

  // ============================================================
  // 10. 注册 CLI 命令
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
  // 11. before_prompt_build（文档化 API: api.on）
  //     三个职责：
  //     a) 从 event 中捕获/更新 session
  //     b) 注入 system prompt（引导绑定 或 功能说明）
  //     c) 推送轮询发现的待办通知（通过 prependContext）
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
            `当前绑定邮箱: ${creds!.email}，后台轮询运行中。`,
            "用户可以直接说「帮我约某某开会」来发起会议协商，",
            "或说「有没有新的会议邀请」来手动检查待办任务。",
            "会议在对话中用标题称呼（如「项目讨论会」），不需要让用户记 ID。",
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

      // --- c) 推送轮询发现的待办通知 ---
      if (pendingNotifications.length > 0) {
        result.prependContext = [
          "[ClawSync 后台轮询发现新任务]",
          ...pendingNotifications,
          "",
          "请调用 check_and_respond_tasks 工具获取详细信息并处理。",
        ].join("\n");
        pendingNotifications = [];
      }

      return result;
    },
    { priority: 5 },
  );

  console.log("[ClawSync] ClawSync Meeting Negotiator 插件已加载。");
}
