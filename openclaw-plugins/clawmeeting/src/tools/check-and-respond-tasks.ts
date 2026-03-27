// ============================================================
// Tool 3: CheckAndRespondTaskTool (check_and_respond_tasks)
// API 6 + API 5:
//   GET  /api/tasks/pending          — Fetch pending tasks
//   POST /api/meetings/{id}/submit   — Submit response
//
// Core workflow:
//   Poll for tasks → dispatch by task_type:
//   INITIAL_SUBMIT     → Submit available time slots
//   COUNTER_PROPOSAL   → Notify user, wait for decision, then submit via this tool
//
// Two usage modes:
//   Mode A: No params → fetch pending task list
//   Mode B: With params → submit response for a specific meeting
//
// Response types aligned with server schemas.py ResponseType enum
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import type {
  PendingTask,
  ResponseType,
  TimeSlot,
} from "../types/index.js";


/** Tool JSON Schema definition */
export const checkAndRespondTasksSchema = {
  name: "check_and_respond_tasks",
  description: [
    "Check and respond to pending meeting negotiation tasks.",
    "",
    "Mode A - View pending tasks (no params):",
    "  Fetches the pending task list from the server. Use your memory and the user's calendar to handle them.",
    "",
    "Mode B - Submit response (with params):",
    "  Submit a response for a specific meeting. Must provide meeting_id and response_type.",
    "",
    "response_type options:",
    "  INITIAL          — First-time submission of available time slots (requires available_slots)",
    "  NEW_PROPOSAL     — Resubmit time slots during negotiation (requires available_slots)",
    "  ACCEPT_PROPOSAL  — Accept the coordinator's compromise proposal (no available_slots needed)",
    "  REJECT           — Decline participation; records rejection but does not immediately terminate the meeting",
    "",
    "Workflow:",
    "  1. On INITIAL_SUBMIT: select available time slots based on user's memory and calendar, then submit",
    "  2. On [ClawMeeting Negotiation Update]: coordinator's compromise proposal has been pushed to you",
    "  3. Present the proposal to the user and wait for their decision:",
    "     - User accepts → call this tool with response_type='ACCEPT_PROPOSAL'",
    "     - User proposes new times → call this tool with response_type='NEW_PROPOSAL' + available_slots",
    "     - User rejects → call this tool with response_type='REJECT'",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      meeting_id: {
        type: "string" as const,
        description:
          "Meeting ID to respond to. If omitted, only fetches the task list.",
      },
      response_type: {
        type: "string" as const,
        enum: ["INITIAL", "NEW_PROPOSAL", "ACCEPT_PROPOSAL", "REJECT"],
        description: [
          "Response type (required when meeting_id is provided):",
          "INITIAL - first-time availability submission;",
          "NEW_PROPOSAL - resubmit during negotiation;",
          "ACCEPT_PROPOSAL - accept coordinator's proposal;",
          "REJECT - decline, records rejection without immediately terminating the meeting.",
        ].join(" "),
      },
      available_slots: {
        type: "array" as const,
        items: {
          type: "object" as const,
          properties: {
            start: {
              type: "string" as const,
              description: "Slot start time, format: 'YYYY-MM-DD HH:MM' (00:00-23:59, never use 24:00)",
            },
            end: {
              type: "string" as const,
              description: "Slot end time, format: 'YYYY-MM-DD HH:MM' (00:00-23:59, never use 24:00)",
            },
          },
          required: ["start", "end"],
        },
        description:
          "Available time slots (required for INITIAL and NEW_PROPOSAL). Pass {start, end} objects; the tool converts them to the server's string format internally.",
      },
      preference_note: {
        type: "string" as const,
        description: "User's preference note or remarks (optional)",
      },
      duration_minutes: {
        type: "number" as const,
        description:
          "New meeting duration in minutes (optional, only for FAILED→retry: initiator can modify the meeting duration)",
      },
      invitees: {
        type: "array" as const,
        items: { type: "string" as const },
        description:
          "New invitee email list (optional, only for FAILED→retry: initiator can add/remove participants)",
      },
    },
    required: [],
  },
};

// ---- Internal: build INITIAL_SUBMIT task info ----
function buildInitialSubmitInfo(task: PendingTask): object {
  const initiatorSlots = (task as any).initiator_slots ?? [];
  const slotsDisplay = initiatorSlots.length > 0
    ? `Organizer's proposed slots: ${initiatorSlots.join(", ")}`
    : "Organizer did not provide specific time slots";

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_AGENT_ACTION",
    task_type: "INITIAL_SUBMIT",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    initiator_slots: initiatorSlots,
    server_message: task.message,
    display_to_user: [
      `📅 Meeting invitation: "${task.title}"`,
      `Organizer: ${task.initiator}`,
      `Duration: ${task.duration_minutes ?? "unknown"} minutes`,
      slotsDisplay,
      "Please tell me when you're available and I'll submit for you.",
    ].join("\n"),
    instruction: [
      "Received a meeting invitation. Silently handle it without showing the raw invitation to the user. ",
      "Follow this exact order: ",
      "Step 1: Check if the user has a connected calendar. If yes, you MUST query it to get their real schedule. ",
      "Step 2: Check your memory for schedule info the user mentioned (business trips, doctor appointments, picking up kids, dinner plans, etc.) and meeting preferences. ",
      "Step 3: If calendar or memory provides schedule info, combine them with the organizer's proposed slots to select suitable times, ",
      "then call this tool to submit: meeting_id, response_type='INITIAL', available_slots, preference_note. ",
      "【Important】If you handled this entirely by yourself (user was NOT asked), you MUST prefix preference_note with '[auto]'. ",
      "Example: preference_note='[auto] 根据日历和记忆自动安排，周三下午有空'. ",
      "After successful submission, briefly inform the user: which meeting was received, what times you submitted, and why. ",
      "Step 4: If you have NO connected calendar AND your memory has NO schedule info at all, ",
      "then ask the user for their available times before submitting. ",
      "【Critical】Always fill in preference_note with any scheduling constraints, preferences, or context. ",
      "If the user provided input directly, do NOT add [auto] prefix. ",
      "Examples: ",
      "  - Schedule constraint: '下午有课，只有上午有空' ",
      "  - Partial attendance: '我可以参加，但只能待30分钟' ",
      "  - Format change: '我在外地出差，建议改成线上会议' ",
      "  - Delegate: '我去不了，建议让 xxx@example.com 代替我参加' ",
      "Only fill it when relevant; omit it otherwise.",
    ].join(""),
  };
}

// ---- Internal: build COUNTER_PROPOSAL task info ----
function buildCounterProposalInfo(task: PendingTask): object {
  const suggestedSlots = (task as any).suggested_slots ?? [];
  const slotsText = suggestedSlots.length > 0
    ? `Suggested slots: ${suggestedSlots.join(", ")}`
    : "";

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_USER_DECISION",
    task_type: "COUNTER_PROPOSAL",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    suggested_slots: suggestedSlots,
    coordinator_message: task.message,
    display_to_user: [
      `🔄 Meeting "${task.title}" needs negotiation (round ${task.round_count ?? 0})`,
      `Coordinator's proposal: ${task.message}`,
      slotsText,
      "",
      "You can choose: Accept proposal / Submit new times / Reject",
    ].filter(Boolean).join("\n"),
    instruction: [
      "The coordinator has sent a compromise proposal. User decision needed. ",
      "【Important】First show display_to_user content to the user, including suggested time slots. ",
      "Consider the user's memory (preferences, habits, and mentioned schedule) and calendar for reference. ",
      "Then wait for the user to decide: ",
      "  - User accepts → call this tool with meeting_id + response_type='ACCEPT_PROPOSAL' ",
      "  - User wants to change times → call this tool with meeting_id + response_type='NEW_PROPOSAL' + available_slots ",
      "  - User rejects → call this tool with meeting_id + response_type='REJECT' ",
      "【Critical】For ALL response types above, you MUST always fill in preference_note with the user's reasoning, ",
      "constraints, or any additional context they mentioned. Examples: ",
      "  - Accept: preference_note='下午2点最方便' ",
      "  - New proposal: preference_note='周三有课，只能周四' ",
      "  - Reject: preference_note='这周出差，无法参加' ",
      "  - Reject with delegate: preference_note='无法参加，建议让 xxx@example.com 代替我参加' ",
      "  - Partial attendance: preference_note='我可以参加，但只能待30分钟' (user can attend but not the full duration) ",
      "  - Format change: preference_note='我在外地出差，建议改成线上会议' ",
      "  - ANY structural suggestion: preference_note='时间太长了，建议改成30分钟' or '建议把小王也加上' ",
      "This field is critical for the coordinator agent to understand everyone's real constraints. Only fill it when the user expressed something relevant; omit it if the user has no additional context.",
    ].join("\n"),
  };
}

// ---- Internal: build MEETING_FAILED task info ----
function buildFailedInfo(task: PendingTask): object {
  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_USER_DECISION",
    task_type: "MEETING_FAILED",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    server_message: task.message,
    display_to_user: [
      `❌ Meeting "${task.title}" negotiation failed`,
      `Reason: ${task.message ?? "Unable to find a common time."}`,
      "",
      "You can choose:",
      "1. Cancel this meeting",
      "2. Modify meeting parameters (times, duration, participants) and start a new round of negotiation",
    ].join("\n"),
    instruction: [
      "Meeting negotiation has failed. The initiator needs to decide next steps. ",
      "【Important】Show the display_to_user content to the user, including the failure reason. ",
      "Then CLEARLY tell the user they have TWO options: ",
      "  Option 1: Cancel this meeting entirely. ",
      "  Option 2: Modify meeting parameters and start a new round of negotiation. ",
      "  【You MUST explicitly list ALL changeable parameters to the user】: ",
      "    a) available_slots — your available time slots ",
      "    b) duration_minutes — meeting duration (current: " + String(task.duration_minutes ?? "unknown") + " min) ",
      "    c) invitees — add or remove participants ",
      "  Do NOT only mention time slots. Always mention all three. ",
      "Wait for the user's decision: ",
      "  - Cancel → call this tool with meeting_id + response_type='REJECT' ",
      "  - Retry → ask the user which parameters they want to change, then call this tool with: ",
      "    meeting_id + response_type='INITIAL' + available_slots (required), ",
      "    and optionally duration_minutes and/or invitees if the user wants to change them. ",
      "Do NOT proceed without the user's explicit choice.",
    ].join(""),
  };
}

/** Tool handler function */
export function createCheckAndRespondTasksHandler(
  apiClient: ClawMeetingApiClient,
) {
  return async (params: {
    meeting_id?: string;
    response_type?: ResponseType;
    available_slots?: TimeSlot[];
    preference_note?: string;
    duration_minutes?: number;
    invitees?: string[];
  }) => {
    const { meeting_id, response_type, available_slots, preference_note, duration_minutes, invitees } =
      params;

    // Check Token
    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "Identity not bound yet. Please call bind_identity first.",
      };
    }

    // =============================================
    // Mode B: Submit response for a specific meeting
    // =============================================
    if (meeting_id && response_type) {
      // ACCEPT_PROPOSAL and REJECT don't need available_slots
      if ((response_type === "INITIAL" || response_type === "NEW_PROPOSAL") && !available_slots?.length) {
        return {
          success: false,
          message: `response_type='${response_type}' requires available_slots.`,
        };
      }

      // Convert {start, end} objects to server's string format
      const slotsAsStrings = (available_slots ?? []).map((slot) => {
        if (typeof slot === "string") return slot;
        const startDate = slot.start.split(" ")[0] ?? "";
        const startTime = slot.start.split(" ")[1] ?? slot.start;
        const endTime = slot.end.split(" ")[1] ?? slot.end;
        return `${startDate} ${startTime}-${endTime}`;
      });

      const submitData: Record<string, unknown> = {
        response_type,
        available_slots: slotsAsStrings,
        preference_note,
      };
      // FAILED→retry: pass modified meeting parameters if provided
      if (duration_minutes !== undefined) submitData.duration_minutes = duration_minutes;
      if (invitees !== undefined) submitData.invitees = invitees;

      try {
        const result = await apiClient.submitAvailability(
          meeting_id,
          submitData,
        );
        return {
          success: true,
          meeting_id,
          response_type,
          message: response_type === "ACCEPT_PROPOSAL"
            ? "Accepted the coordinator's proposal."
            : response_type === "REJECT"
              ? "Rejection recorded."
              : "Response submitted.",
          status: result.status,
          all_submitted: result.all_submitted,
        };
      } catch (error: unknown) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return {
          success: false,
          meeting_id,
          message: `Failed to submit response: ${errMsg}`,
        };
      }
    }

    // =============================================
    // Mode A: Fetch pending tasks for Agent to handle
    // =============================================
    try {
      const { pending_tasks } = await apiClient.getPendingTasks();

      if (!pending_tasks || pending_tasks.length === 0) {
        return {
          success: true,
          message: "No pending meeting tasks at this time.",
          pending_count: 0,
        };
      }

      const results: object[] = [];

      for (const task of pending_tasks) {
        switch (task.task_type) {
          case "INITIAL_SUBMIT": {
            let meetingDetail: any = null;
            try {
              meetingDetail = await apiClient.getMeetingDetail(task.meeting_id);
            } catch (e) {
              console.log(`[CM:tool] check_and_respond_tasks: getMeetingDetail 失败, falling back to basic info: ${e}`);
            }

            const info = buildInitialSubmitInfo(task) as any;

            if (meetingDetail?.participants) {
              info.participants_slots = meetingDetail.participants
                .filter((p: any) => p.latest_slots && p.latest_slots.length > 0)
                .map((p: any) => ({
                  email: p.email,
                  role: p.role,
                  latest_slots: p.latest_slots,
                }));
            }

            results.push(info);
            break;
          }
          case "COUNTER_PROPOSAL": {
            let meetingDetail: any = null;
            try {
              meetingDetail = await apiClient.getMeetingDetail(task.meeting_id);
            } catch (e) {
              console.log(`[CM:tool] check_and_respond_tasks: getMeetingDetail 失败, falling back to basic info: ${e}`);
            }

            const info = buildCounterProposalInfo(task) as any;

            if (meetingDetail) {
              info.coordinator_reasoning = meetingDetail.coordinator_reasoning ?? null;
              info.participants_slots = (meetingDetail.participants ?? [])
                .filter((p: any) => p.latest_slots && p.latest_slots.length > 0)
                .map((p: any) => ({
                  email: p.email,
                  role: p.role,
                  latest_slots: p.latest_slots,
                }));
            }

            results.push(info);
            break;
          }
          case "MEETING_FAILED": {
            results.push(buildFailedInfo(task));
            break;
          }
          default:
            // Notification types (CONFIRMED, OVER, etc.) — unified handling
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: task.task_type,
              initiator: task.initiator,
              display_to_user: task.message,
              instruction: "Relay the display_to_user content to the user in natural language.",
            });
            break;
        }
      }

      return {
        success: true,
        pending_count: pending_tasks.length,
        message: `${pending_tasks.length} pending task(s). Please handle each one.`,
        task_results: results,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Failed to fetch pending tasks: ${errMsg}`,
        hint: "Please check network connection and server status.",
      };
    }
  };
}
