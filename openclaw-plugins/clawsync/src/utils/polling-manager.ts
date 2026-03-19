// ============================================================
// ClawSync Plugin - 轮询管理器
// 独立模块，管理后台定时轮询的生命周期
//
// 设计要点（高可扩展性）：
// - 独立于 Tool 和 Hook，可由任意位置触发 start/stop
// - onPoll 回调解耦具体轮询逻辑
// - onAutoRespond 回调：轮询发现任务时
//   插件自行读取日历并自动提交，不唤醒 Agent
//   只在会议确认/无法调和时通过 onNotifyUser 通知用户
// - 支持动态调整轮询间隔（如服务端繁忙时自动退避）
// ============================================================

export interface PollingManagerOptions {
  /** 轮询间隔（毫秒） */
  intervalMs: number;
  /** 是否启用轮询 */
  enabled: boolean;
  /** 每次轮询执行的回调，返回 pending tasks 的原始结果 */
  onPoll: () => Promise<unknown>;
  /**
   * 轮询发现需要处理的任务时调用（异步）
   * 插件自行读日历 + 提交，不通知用户
   * 返回需要通知用户的消息列表（如会议确认/冲突无法解决）
   */
  onAutoRespond?: (tasks: unknown[]) => Promise<string[]>;
  /**
   * 当有需要通知用户的消息时调用
   * 仅用于：会议最终确认、多轮协商无法调和
   */
  onNotifyUser?: (messages: string[]) => void;
  /**
   * [扩展点] 轮询拿到结果后的钩子
   */
  onTaskReceived?: (result: unknown) => void;
}

export class PollingManager {
  private timer: ReturnType<typeof setInterval> | null = null;
  private options: PollingManagerOptions;
  private running = false;

  constructor(options: PollingManagerOptions) {
    this.options = options;
  }

  /** 启动轮询（幂等：重复调用不会创建多个定时器） */
  start(): void {
    if (!this.options.enabled) {
      console.log("[clawsync:polling] 自动响应已禁用，跳过轮询启动。");
      return;
    }

    if (this.running) {
      console.log("[clawsync:polling] 轮询已在运行中，跳过重复启动。");
      return;
    }

    console.log(
      `[clawsync:polling] 启动后台轮询，间隔 ${this.options.intervalMs}ms`,
    );

    this.running = true;
    this.timer = setInterval(async () => {
      try {
        const result = await this.options.onPoll();

        // [扩展点] 通知下游处理器
        this.options.onTaskReceived?.(result);

        // 检查是否有需要处理的任务（包括自动提交和通知类）
        const taskResults = (result as any)?.task_results as unknown[];
        if (taskResults?.length) {
          const actionable = taskResults.filter(
            (t: any) => t.action === "NEEDS_AGENT_ACTION" || t.action === "NOTIFY_USER",
          );
          if (actionable.length > 0 && this.options.onAutoRespond) {
            // 自动处理，不唤醒 Agent
            const userMessages = await this.options.onAutoRespond(actionable);
            // 只有确实需要通知用户的消息才推送
            if (userMessages.length > 0 && this.options.onNotifyUser) {
              this.options.onNotifyUser(userMessages);
            }
          }
        }
      } catch (err) {
        console.error("[clawsync:polling] 轮询出错:", err);
      }
    }, this.options.intervalMs);
  }

  /** 停止轮询 */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      this.running = false;
      console.log("[clawsync:polling] 后台轮询已停止。");
    }
  }

  /** 查询轮询是否正在运行 */
  isRunning(): boolean {
    return this.running;
  }

  /**
   * [扩展点] 动态更新轮询间隔
   * 使用场景：服务端返回 429 时自动退避，空闲时恢复
   */
  updateInterval(newIntervalMs: number): void {
    const wasRunning = this.running;
    this.stop();
    this.options.intervalMs = newIntervalMs;
    if (wasRunning) {
      this.start();
    }
    console.log(
      `[clawsync:polling] 轮询间隔已更新为 ${newIntervalMs}ms`,
    );
  }
}
