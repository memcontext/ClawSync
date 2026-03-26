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

    const startMs = Date.now();
    console.log(`[CM:api] >>> ${method} ${path}${body ? ` body=${JSON.stringify(body).substring(0, 300)}` : ""}`);

    try {
      const res = await fetch(url, options);
      const elapsed = Date.now() - startMs;
      const json = (await res.json()) as ApiResponse<T>;

      if (!res.ok || json.code !== 200) {
        console.error(`[CM:api] <<< ${method} ${path} ${res.status} code=${json.code} msg="${json.message}" (${elapsed}ms)`);
        throw new Error(
          `API Error [${method} ${path}]: ${json.message ?? res.statusText}`,
        );
      }

      console.log(`[CM:api] <<< ${method} ${path} ${res.status} code=${json.code} (${elapsed}ms) data=${JSON.stringify(json.data).substring(0, 200)}`);
      return json;
    } catch (err) {
      const elapsed = Date.now() - startMs;
      if ((err as Error)?.message?.startsWith("API Error")) throw err;
      console.error(`[CM:api] <<< ${method} ${path} NETWORK_ERROR (${elapsed}ms): ${(err as Error)?.message}`);
      throw err;
    }
  }

  // ============================================================
  // API 1a: POST /api/auth/send-code — 发送验证码
  // ============================================================
  async sendVerificationCode(email: string): Promise<{ message: string }> {
    const url = `${this.baseUrl}/api/auth/send-code`;
    console.log(`[CM:api] >>> POST /api/auth/send-code email=${email}`);
    const startMs = Date.now();
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email } as SendCodeRequest),
    });
    const json = (await res.json()) as ApiResponse<null>;
    const elapsed = Date.now() - startMs;
    console.log(`[CM:api] <<< POST /api/auth/send-code ${res.status} code=${json.code} msg="${json.message}" (${elapsed}ms)`);

    // send-code 限频时返回 HTTP 200 但 code=429，验证失败 code=400/500
    // 统一将 message 返回给上层，不抛异常
    return { message: json.message ?? "验证码已发送" };
  }

  // ============================================================
  // API 1b: POST /api/auth/verify-bind — 验证码校验 + 绑定注册
  // ============================================================
  async verifyAndBind(email: string, code: string): Promise<{ success: boolean; message: string; data?: BindAuthResponse }> {
    const url = `${this.baseUrl}/api/auth/verify-bind`;
    console.log(`[CM:api] >>> POST /api/auth/verify-bind email=${email} code=${code}`);
    const startMs = Date.now();
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code } as VerifyBindRequest),
    });
    const json = (await res.json()) as ApiResponse<BindAuthResponse>;
    const elapsed = Date.now() - startMs;
    console.log(`[CM:api] <<< POST /api/auth/verify-bind ${res.status} code=${json.code} msg="${json.message}" hasToken=${!!json.data?.token} (${elapsed}ms)`);

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
