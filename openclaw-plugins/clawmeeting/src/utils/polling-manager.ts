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
  private cycleCount = 0;
  private skipCount = 0;

  constructor(options: PollingManagerOptions) {
    this.options = options;
  }

  start(): void {
    if (!this.options.enabled) {
      console.log(`[CM:poll] start() 被跳过: enabled=false`);
      return;
    }
    if (this.running) {
      console.log(`[CM:poll] start() 被跳过: 已在运行中`);
      return;
    }

    console.log(`[CM:poll] 启动后台轮询，间隔 ${this.options.intervalMs}ms`);
    this.running = true;
    this.cycleCount = 0;
    this.skipCount = 0;

    this.timer = setInterval(async () => {
      // 防并发：上一次还没完成就跳过
      if (this.polling) {
        this.skipCount++;
        if (this.skipCount % 5 === 0) {
          console.log(`[CM:poll] 并发跳过累计 ${this.skipCount} 次（上一次轮询仍在执行）`);
        }
        return;
      }
      this.polling = true;
      this.cycleCount++;
      const cycleId = this.cycleCount;
      const startMs = Date.now();

      try {
        const result = await this.options.onPoll();
        const elapsed = Date.now() - startMs;
        const taskResults = (result as any)?.task_results as unknown[];
        const pendingCount = (result as any)?.pending_count ?? 0;

        if (taskResults?.length) {
          console.log(`[CM:poll] #${cycleId} 完成 (${elapsed}ms) → ${taskResults.length} 个新任务待处理 (pending_count=${pendingCount})`);
        } else if (cycleId % 30 === 0) {
          // 每 30 个周期打印一次心跳，避免日志过多
          console.log(`[CM:poll] #${cycleId} 心跳 (${elapsed}ms) 无新任务`);
        }

        this.options.onTaskReceived?.(result);

        if (taskResults?.length && this.options.onAutoRespond) {
          console.log(`[CM:poll] #${cycleId} 开始 autoRespond，共 ${taskResults.length} 个任务`);
          const respondStart = Date.now();
          const userMessages = await this.options.onAutoRespond(taskResults);
          console.log(`[CM:poll] #${cycleId} autoRespond 完成 (${Date.now() - respondStart}ms)，fallback 消息数=${userMessages.length}`);
          if (userMessages.length > 0 && this.options.onNotifyUser) {
            this.options.onNotifyUser(userMessages);
          }
        }
      } catch (err) {
        const elapsed = Date.now() - startMs;
        console.error(`[CM:poll] #${cycleId} 轮询出错 (${elapsed}ms):`, err);
      } finally {
        this.polling = false;
      }
    }, this.options.intervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      console.log(`[CM:poll] 后台轮询已停止。共执行 ${this.cycleCount} 个周期，跳过 ${this.skipCount} 次`);
      this.running = false;
      this.polling = false;
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
