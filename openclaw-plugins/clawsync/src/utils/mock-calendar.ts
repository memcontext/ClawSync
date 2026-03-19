// ============================================================
// ClawSync Plugin - Mock 日历与上下文数据
// MVP 阶段使用写死的模拟数据，后续替换为真实日历集成
//
// 注意: available_slots 格式已对齐服务端 API_REFERENCE.md
//       使用 { start, end } 对象，不再使用 "14:00-17:00" 字符串
// ============================================================

import type { TimeSlot, CalendarSlot, UserPreferences } from "../types/index.js";

/**
 * Mock: 获取用户未来 3 天的日历空闲时段
 * 返回格式与服务端 API 5 要求的 available_slots 对齐
 * e.g. { start: "2026-03-18 14:00", end: "2026-03-18 17:00" }
 */
export function getMockAvailableSlots(): TimeSlot[] {
  const today = new Date();
  const slots: TimeSlot[] = [];

  // 只有今天有空闲，明天和后天没时间
  const dateStr = today.toISOString().split("T")[0];
  slots.push({ start: `${dateStr} 10:00`, end: `${dateStr} 12:00` });
  slots.push({ start: `${dateStr} 14:00`, end: `${dateStr} 17:00` });

  return slots;
}

/**
 * Mock: 获取发起会议时的 initiator_data.available_slots
 * API 2 的 initiator_data.available_slots 仍为字符串数组格式
 * e.g. "2026-03-18 14:00-18:00"
 */
export function getMockAvailableSlotsAsStrings(): string[] {
  const today = new Date();
  const dateStr = today.toISOString().split("T")[0];

  // 只有今天有空闲，明天和后天没时间
  return [
    `${dateStr} 10:00-12:00`,
    `${dateStr} 14:00-17:00`,
  ];
}

/**
 * Mock: 获取用户当前日历的繁忙时段（供分析冲突用）
 */
export function getMockBusySlots(): CalendarSlot[] {
  const today = new Date();
  const dateStr = today.toISOString().split("T")[0];

  return [
    { date: dateStr, start: "09:00", end: "10:00", is_busy: true },
    { date: dateStr, start: "12:00", end: "13:30", is_busy: true },
  ];
}

/**
 * Mock: 用户的长期偏好（后续从 storage 读取并由 Agent 沉淀）
 */
export function getMockUserPreferences(): UserPreferences {
  return {
    disliked_times: ["早上9点前", "周五下午"],
    preferred_times: ["下午2点-5点", "周二周四上午"],
    buffer_minutes: 15,
    notes: [
      "不喜欢早会",
      "连续会议之间需要15分钟缓冲",
      "周五下午尽量不安排会议",
    ],
  };
}

/**
 * 将偏好信息格式化为自然语言，供 Agent 在协商时参考
 */
export function formatPreferencesForAgent(prefs: UserPreferences): string {
  const lines: string[] = ["[用户会议偏好]"];

  if (prefs.disliked_times?.length) {
    lines.push(`不喜欢的时间: ${prefs.disliked_times.join("、")}`);
  }
  if (prefs.preferred_times?.length) {
    lines.push(`偏好时间: ${prefs.preferred_times.join("、")}`);
  }
  if (prefs.buffer_minutes) {
    lines.push(`会议间缓冲: ${prefs.buffer_minutes} 分钟`);
  }
  if (prefs.notes?.length) {
    lines.push(`其他习惯: ${prefs.notes.join("；")}`);
  }

  return lines.join("\n");
}
