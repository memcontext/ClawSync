// ============================================================
// ClawSync Plugin - 类型定义
// 严格对齐服务端 API_REFERENCE.md v1.0.0
// ============================================================

// ---- 服务端 API 通用响应 ----
export interface ApiResponse<T = unknown> {
  code: number;
  data?: T;
  message?: string;
}

// ---- API 1: POST /api/auth/bind 身份认证 ----
export interface BindAuthRequest {
  email: string;
}

export interface BindAuthResponse {
  token: string;
  user_id: number;
}

// ---- API 2: POST /api/meetings 发起会议协商 ----
export interface InitiateMeetingRequest {
  title: string;
  duration_minutes: number;
  invitees: string[];
  initiator_data: {
    available_slots: string[]; // e.g. "2026-03-18 14:00-18:00"
    preference_note?: string;
  };
}

export interface InitiateMeetingResponse {
  id: string;           // 服务端返回字段名为 "id"，如 "mtg_a1b2c3d4e5f67890"
  title: string;
  status: string;       // "COLLECTING"
  duration_minutes: number;
  invitees: string[];
  initiator_data: {
    available_slots: string[];
    preference_note?: string;
  };
}

// ---- API 5: POST /api/meetings/{id}/submit 提交空闲时间 ----

/** 时间段对象，与服务端格式对齐 */
export interface TimeSlot {
  start: string;  // "2026-03-18 14:00"
  end: string;    // "2026-03-18 16:00"
}

/** 提交类型: INITIAL(首次) / COUNTER(协商轮次) */
export type ResponseType = "INITIAL" | "COUNTER";

export interface SubmitAvailabilityRequest {
  response_type: ResponseType;
  available_slots: TimeSlot[];
  preference_note?: string;
}

export interface SubmitAvailabilityResponse {
  id: string;
  response_type: ResponseType;
  status: string;
  all_submitted: boolean;
  coordinator_result?: {
    status: string;
    final_time?: string;
    reasoning?: string;
    suggestions?: string[];
    alternative_slots?: string[];
  };
  created_at: string;
  updated_at: string;
}

// ---- API 6: GET /api/tasks/pending 获取待办任务 ----
export type TaskType = "INITIAL_SUBMIT" | "COUNTER_PROPOSAL";

export interface PendingTask {
  meeting_id: string;
  title: string;
  initiator: string;
  task_type: TaskType;
  message: string;
}

export interface PendingTasksResponse {
  pending_tasks: PendingTask[];
}

// ---- 插件内部: 本地存储的用户认证信息 ----
export interface StoredCredentials {
  email: string;
  token: string;
  user_id: number;
  /** 绑定时的 session key，用于轮询推送消息回到同一 session */
  sessionKey?: string;
}

// ---- 插件内部: 用户偏好/长期记忆 ----
export interface UserPreferences {
  disliked_times?: string[];   // e.g. ["早上9点前", "周五下午"]
  preferred_times?: string[];  // e.g. ["下午2-5点"]
  buffer_minutes?: number;     // 会议间缓冲时间
  notes?: string[];            // 其他习惯备注
}

// ---- 插件内部: Mock 日历数据 ----
export interface CalendarSlot {
  date: string;       // "2026-03-18"
  start: string;      // "14:00"
  end: string;        // "18:00"
  is_busy: boolean;
}

// ---- 插件内部: Session 上下文 ----
// 记录用户发起绑定时的 session 信息
// 后续轮询推送消息时回到同一个 session，避免创建新对话
export interface SessionContext {
  /** OpenClaw session key, e.g. "agent:main:webchat:dm:alice" */
  sessionKey?: string;
  /** 消息来源渠道 */
  channel?: string;
  /** 对话对象标识 */
  peerId?: string;
}

// ---- 插件配置 ----
export interface ClawSyncPluginConfig {
  serverUrl: string;
  pollingIntervalMs: number;
  autoRespond: boolean;
}
