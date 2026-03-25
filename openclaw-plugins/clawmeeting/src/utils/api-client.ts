// ============================================================
// ClawMeeting Plugin - API Client 工具层
// 封装与中央协调端 (API Server) 的所有 HTTP 通信
// 严格对齐 API_REFERENCE.md v1.0.0
// ============================================================

import type {
  ApiResponse,
  BindAuthRequest,
  BindAuthResponse,
  SendCodeRequest,
  VerifyBindRequest,
  InitiateMeetingRequest,
  InitiateMeetingResponse,
  MeetingListResponse,
  MeetingDetailResponse,
  PendingTasksResponse,
  SubmitAvailabilityRequest,
  SubmitAvailabilityResponse,
} from "../types/index.js";

export class ClawMeetingApiClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  /** 设置认证 Token（绑定成功后调用） */
  setToken(token: string) {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  // ---- 通用请求方法 ----
  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<ApiResponse<T>> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const options: RequestInit = { method, headers };
    if (body && (method === "POST" || method === "PUT" || method === "PATCH")) {
      options.body = JSON.stringify(body);
    }

    const res = await fetch(url, options);
    const json = (await res.json()) as ApiResponse<T>;

    if (!res.ok || json.code !== 200) {
      throw new Error(
        `API Error [${method} ${path}]: ${json.message ?? res.statusText}`,
      );
    }

    return json;
  }

  // ============================================================
  // API 1a: POST /api/auth/send-code — 发送验证码
  // ============================================================
  async sendVerificationCode(email: string): Promise<{ message: string }> {
    const url = `${this.baseUrl}/api/auth/send-code`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email } as SendCodeRequest),
    });
    const json = (await res.json()) as ApiResponse<null>;

    // send-code 限频时返回 HTTP 200 但 code=429，验证失败 code=400/500
    // 统一将 message 返回给上层，不抛异常
    return { message: json.message ?? "验证码已发送" };
  }

  // ============================================================
  // API 1b: POST /api/auth/verify-bind — 验证码校验 + 绑定注册
  // ============================================================
  async verifyAndBind(email: string, code: string): Promise<{ success: boolean; message: string; data?: BindAuthResponse }> {
    const url = `${this.baseUrl}/api/auth/verify-bind`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code } as VerifyBindRequest),
    });
    const json = (await res.json()) as ApiResponse<BindAuthResponse>;

    // verify-bind 验证失败时返回 HTTP 200 但 code=400
    if (json.code !== 200 || !json.data?.token) {
      return { success: false, message: json.message ?? "验证失败" };
    }

    this.setToken(json.data.token);
    return { success: true, message: json.message ?? "验证成功", data: json.data };
  }

  // ============================================================
  // API 1 (Deprecated): POST /api/auth/bind — 直接绑定（无验证）
  // ============================================================
  async bindEmail(email: string): Promise<BindAuthResponse> {
    const payload: BindAuthRequest = { email };
    const res = await this.request<BindAuthResponse>(
      "POST",
      "/api/auth/bind",
      payload,
    );
    if (res.data?.token) {
      this.setToken(res.data.token);
    }
    return res.data!;
  }

  // ============================================================
  // API 2: POST /api/meetings — 发起会议协商
  // ============================================================
  async initiateMeeting(
    data: InitiateMeetingRequest,
  ): Promise<InitiateMeetingResponse> {
    const res = await this.request<InitiateMeetingResponse>(
      "POST",
      "/api/meetings",
      data,
    );
    return res.data!;
  }

  // ============================================================
  // API 3: GET /api/meetings — 我的会议列表
  // ============================================================
  async getMeetingList(): Promise<MeetingListResponse> {
    const res = await this.request<MeetingListResponse>(
      "GET",
      "/api/meetings",
    );
    return res.data!;
  }

  // ============================================================
  // API 4: GET /api/meetings/{meeting_id} — 查询会议详情
  // ============================================================
  async getMeetingDetail(meetingId: string): Promise<MeetingDetailResponse> {
    const res = await this.request<MeetingDetailResponse>(
      "GET",
      `/api/meetings/${meetingId}`,
    );
    return res.data!;
  }

  // ============================================================
  // API 5: POST /api/meetings/{meeting_id}/submit — 提交空闲时间
  // ============================================================
  async submitAvailability(
    meetingId: string,
    data: SubmitAvailabilityRequest,
  ): Promise<SubmitAvailabilityResponse> {
    const res = await this.request<SubmitAvailabilityResponse>(
      "POST",
      `/api/meetings/${meetingId}/submit`,
      data,
    );
    return res.data!;
  }

  // ============================================================
  // API 6: GET /api/tasks/pending — 获取待办任务（轮询）
  // ============================================================
  async getPendingTasks(): Promise<PendingTasksResponse> {
    const res = await this.request<PendingTasksResponse>(
      "GET",
      "/api/tasks/pending",
    );
    return res.data!;
  }
}
