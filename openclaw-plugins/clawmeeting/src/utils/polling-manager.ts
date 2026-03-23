// ============================================================
// ClawMeeting Plugin - 轮询管理器
// 独立模块，管理后台定时轮询的生命周期
//
// 设计要点：
// - 独立于 Tool 和 Hook，可由任意位置触发 start/stop
// - onPoll + onAutoRespond 解耦轮询与处理逻辑
// - 防并发：上一次轮询未完成时跳过本次
// ============================================================

export interface PollingManagerOptions {
  intervalMs: number;
  enabled: boolean;
  onPoll: () => Promise<unknown>;
  onAutoRespond?: (tasks: unknown[]) => Promise<string[]>;
  onNotifyUser?: (messages: string[]) => void;
  onTaskReceived?: (result: unknown) => void;
}

export class PollingManager {
  private timer: ReturnType<typeof setInterval> | null = null;
  private options: PollingManagerOptions;
  private running = false;
  private polling = false; // 防并发锁

  constructor(options: PollingManagerOptions) {
    this.options = options;
  }

  start(): void {
    if (!this.options.enabled) return;
    if (this.running) return;

    console.log(`[clawmeeting:polling] 启动后台轮询，间隔 ${this.options.intervalMs}ms`);
    this.running = true;

    this.timer = setInterval(async () => {
      // 防并发：上一次还没完成就跳过
      if (this.polling) return;
      this.polling = true;

      try {
        const result = await this.options.onPoll();
        this.options.onTaskReceived?.(result);

        const taskResults = (result as any)?.task_results as unknown[];
        if (taskResults?.length && this.options.onAutoRespond) {
          const userMessages = await this.options.onAutoRespond(taskResults);
          if (userMessages.length > 0 && this.options.onNotifyUser) {
            this.options.onNotifyUser(userMessages);
          }
        }
      } catch (err) {
        console.error("[clawmeeting:polling] 轮询出错:", err);
      } finally {
        this.polling = false;
      }
    }, this.options.intervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      this.running = false;
      this.polling = false;
      console.log("[clawmeeting:polling] 后台轮询已停止。");
    }
  }

  isRunning(): boolean {
    return this.running;
  }

  updateInterval(newIntervalMs: number): void {
    const wasRunning = this.running;
    this.stop();
    this.options.intervalMs = newIntervalMs;
    if (wasRunning) this.start();
  }
}
