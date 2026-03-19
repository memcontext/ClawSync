// ============================================================
// ClawSync Plugin - 入口文件
// OpenClaw 插件注册点：注册 3 个核心 Tools + 轮询管理器
//
// Session 策略：
//   用户绑定身份时捕获当前 session 信息并持久化。
//   后续轮询发现需要用户决策的任务时，通过 api.sendMessage
//   向同一个 session 推送通知，确保消息不会跑到别的对话窗口。
// ============================================================

import { ClawSyncApiClient } from "./src/utils/api-client.js";
import {
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
  serverUrl: "http://localhost:8000",
  pollingIntervalMs: 10000,
  autoRespond: true,
};

export default function register(api: any) {
  // ============================================================
  // 1. 读取插件配置
  // ============================================================
  const pluginConfig: ClawSyncPluginConfig = {
    ...DEFAULT_CONFIG,
    ...(api.config ?? {}),
  };

  // ============================================================
  // 2. 初始化 API Client
  // ============================================================
  const apiClient = new ClawSyncApiClient(pluginConfig.serverUrl);

  // 尝试从本地恢复已保存的 Token
  const savedCreds = loadCredentials();
  if (savedCreds?.token) {
    apiClient.setToken(savedCreds.token);
    console.log(
      `[ClawSync] 已从本地恢复身份凭证: ${savedCreds.email} (user_id: ${savedCreds.user_id})`,
    );
  }

  // ============================================================
  // 3. Session 管理
  //    从本地恢复上次绑定时记录的 session 上下文
  //    如果还没有，等 bind_identity 调用时捕获
  // ============================================================
  let sessionCtx: SessionContext | null = loadSession();
  if (sessionCtx?.sessionKey) {
    console.log(`[ClawSync] 已恢复 session: ${sessionCtx.sessionKey}`);
  }

  /**
   * 从 OpenClaw Tool 调用的 context 中提取 session 信息
   * OpenClaw 在调用 Tool handler 时会传入 ctx 作为第二个参数，
   * 其中包含 sessionKey, channel, peerId 等信息
   */
  function captureSession(ctx: any): void {
    if (!ctx) return;
    const newSession: SessionContext = {
      sessionKey: ctx.sessionKey ?? ctx.session?.key,
      channel: ctx.channel,
      peerId: ctx.peerId ?? ctx.peer?.id,
    };
    // 只在有有效 sessionKey 时保存
    if (newSession.sessionKey) {
      sessionCtx = newSession;
      saveSession(newSession);
      console.log(`[ClawSync] 已捕获 session: ${newSession.sessionKey}`);
    }
  }

  /**
   * 向用户 session 推送一条消息，唤醒 Agent 处理
   * 利用 OpenClaw 的 api.sendMessage 注入消息到指定 session
   */
  function pushMessageToUser(text: string): void {
    if (!sessionCtx?.sessionKey) {
      console.warn(
        "[ClawSync] 无法推送消息：尚未捕获用户 session。请先完成身份绑定。",
      );
      return;
    }

    try {
      // OpenClaw api.sendMessage 向指定 session 注入一条消息
      // Agent 会被唤醒来处理这条消息并回复用户
      api.sendMessage?.({
        sessionKey: sessionCtx.sessionKey,
        channel: sessionCtx.channel,
        peerId: sessionCtx.peerId,
        text,
      });
      console.log(
        `[ClawSync] 已推送消息到 session ${sessionCtx.sessionKey}`,
      );
    } catch (err) {
      console.error("[ClawSync] 推送消息失败:", err);
    }
  }

  // ============================================================
  // 4. 初始化轮询管理器
  //    不在这里启动，统一由 after_agent_start 钩子启动
  //    确保 Gateway 完全就绪后再开始发请求
  // ============================================================
  const taskHandler = createCheckAndRespondTasksHandler(apiClient);
  const pollingManager = new PollingManager({
    intervalMs: pluginConfig.pollingIntervalMs,
    enabled: pluginConfig.autoRespond,
    onPoll: async () => {
      const result = await taskHandler({});
      if ((result as any).pending_count > 0) {
        console.log(
          `[ClawSync] 轮询发现 ${(result as any).pending_count} 个待办任务`,
        );
      }
      return result;
    },
    // 轮询发现任务时，向用户 session 推送通知，唤醒 Agent 处理
    onNeedAgentAction: (tasks: unknown[]) => {
      for (const task of tasks) {
        const t = task as any;
        const title = t.title ?? "未知会议";
        const taskType = t.task_type;

        if (taskType === "INITIAL_SUBMIT") {
          // Agent 需要读取日历并提交空闲时间
          pushMessageToUser(
            `[会议邀请] 「${title}」${t.server_message ?? "你收到了一个会议邀请，请提交空闲时间。"}\n\n` +
            `请帮我查看日历，选择合适的时间段提交。`,
          );
        } else if (taskType === "COUNTER_PROPOSAL") {
          // Agent 需要展示协调方建议并等用户决策
          pushMessageToUser(
            `[协商通知] 「${title}」${t.coordinator_message ?? "协调方发来了新的协商建议。"}\n\n` +
            `请告诉我你的决定。`,
          );
        }
      }
    },
  });

  // ============================================================
  // 5. 生命周期钩子: Gateway 启动 → 启动轮询
  //    轮询的完整生命周期与 Gateway 绑定:
  //    after_agent_start  → 有 Token 就开始轮询
  //    before_agent_stop  → 清理定时器
  //
  //    如果此时还没绑定（没有 Token），轮询不会启动，
  //    等用户调 bind_identity 成功后再通过回调启动。
  // ============================================================
  api.registerHook?.("after_agent_start", () => {
    if (apiClient.getToken()) {
      console.log("[ClawSync] Gateway 已就绪，启动后台轮询。");
      pollingManager.start();
    } else {
      console.log("[ClawSync] Gateway 已就绪，但尚未绑定身份，轮询待命中。");
    }
  });

  api.registerHook?.("before_agent_stop", () => {
    pollingManager.stop();
  });

  // ============================================================
  // 6. 注册 Tool 1: bind_identity (身份绑定)
  //    绑定成功后：捕获 session + 启动轮询
  // ============================================================
  api.registerTool({
    ...bindIdentitySchema,
    handler: (params: any, ctx: any) => {
      // 捕获当前 session（绑定时的对话就是主 session）
      captureSession(ctx);

      const handler = createBindIdentityHandler(apiClient, () => {
        pollingManager.start();
      });
      return handler(params);
    },
  });

  // ============================================================
  // 7. 注册 Tool 2: initiate_meeting (发起会议协商)
  //    调用时也捕获 session（兜底：万一绑定时没捕获到）
  // ============================================================
  api.registerTool({
    ...initiateMeetingSchema,
    handler: (params: any, ctx: any) => {
      captureSession(ctx);
      const handler = createInitiateMeetingHandler(apiClient);
      return handler(params);
    },
  });

  // ============================================================
  // 8. 注册 Tool 3: check_and_respond_tasks (轮询+响应)
  // ============================================================
  api.registerTool({
    ...checkAndRespondTasksSchema,
    handler: createCheckAndRespondTasksHandler(apiClient),
  });

  // ============================================================
  // 9. 注册 CLI 命令
  // ============================================================
  api.registerCli?.(
    ({ program }: any) => {
      program
        .command("ClawSync-status")
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
            console.log(`绑定 Session: ${session.sessionKey}`);
          } else {
            console.log("Session: 未捕获");
          }
        });
    },
    { commands: ["ClawSync-status"] },
  );

  // ============================================================
  // 10. 注册 Agent System Prompt 注入
  //     让 Agent 了解 ClawSync 插件的能力，并在用户未绑定时主动引导
  // ============================================================
  api.registerHook?.("before_agent_start", (ctx: any) => {
    const creds = loadCredentials();
    const isBound = !!creds?.token;

    const systemPromptAddon = isBound
      ? [
          "[ClawSync 会议助手已就绪]",
          `当前绑定邮箱: ${creds!.email}，后台轮询运行中。`,
          '用户可以直接说「帮我约某某开会」来发起会议协商，',
          '或说「有没有新的会议邀请」来手动检查待办任务。',
          '会议在对话中用标题称呼（如「项目讨论会」），不需要让用户记 ID。',
        ].join("\n")
      : [
          "[ClawSync 会议助手 - 需要初始化]",
          "用户尚未绑定身份。请友好地引导用户提供邮箱来完成绑定。",
          '你可以说：「我是你的会议助手 ClawSync，可以帮你自动约会议、处理邀请。',
          '要开始使用，请先告诉我你的邮箱地址，我来帮你注册。」',
          "",
          "绑定成功后用户可以：",
          '- 一句话发起会议（如「帮我约 Bob 明天开会」）',
          "- 自动处理收到的会议邀请（后台静默完成）",
          "- 收到妥协建议时在这里做决定",
        ].join("\n");

    // 注入到 Agent 的 system prompt 中
    ctx?.addSystemPrompt?.(systemPromptAddon);
  });

  console.log("[ClawSync] ClawSync Meeting Negotiator 插件已加载。");
}
