// ============================================================
// Tool 2: InitiateMeetingTool (initiate_meeting)
// API 2: POST /api/meetings
//
// Converts natural language meeting requests into structured API calls.
// The Agent parses title, duration, invitees, and time slots from user input.
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import type { InitiateMeetingRequest } from "../types/index.js";

/** Tool JSON Schema definition */
export const initiateMeetingSchema = {
  name: "initiate_meeting",
  description: [
    "Initiate a new meeting negotiation.",
    "Converts the user's scheduling request into a structured API call.",
    "The server will notify all invitees' OpenClaw plugins to collect their availability.",
    "",
    "All parameters are required. The user describes their needs in natural language,",
    "e.g.: 'Schedule a 30-min architecture review with bob@x.com and charlie@x.com tomorrow 2-5pm'",
    "You need to parse title, duration_minutes, invitees, and available_slots from the description.",
    "",
    "If any of the following are missing, ask the user — do not assume:",
    "  - Meeting duration (duration_minutes)",
    "  - Organizer's available time slots (available_slots)",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      title: {
        type: "string" as const,
        description: "Meeting title, e.g. 'Architecture Review'",
      },
      duration_minutes: {
        type: "number" as const,
        description: "Meeting duration in minutes. Parse from user description, e.g. 'half an hour' → 30, 'one hour' → 60",
      },
      invitees: {
        type: "array" as const,
        items: { type: "string" as const },
        description:
          "List of invitee email addresses, e.g. ['bob@example.com', 'charlie@example.com']",
      },
      available_slots: {
        type: "array" as const,
        items: { type: "string" as const },
        description: [
          "Organizer's available time slots (required). Parse from the user's natural language description.",
          "Format: 'YYYY-MM-DD HH:MM-HH:MM', e.g. '2026-03-20 14:00-17:00'. Time range 00:00-23:59, never use 24:00.",
          "The user might say 'tomorrow 2pm to 5pm' — convert to a concrete date and time.",
          "Multiple time slots are allowed.",
        ].join(" "),
      },
      preference_note: {
        type: "string" as const,
        description:
          "Organizer's scheduling preferences (optional). Only fill this if your memory genuinely contains the user's meeting preferences, e.g. they mentioned disliking early meetings or no meetings on Fridays. If you have no such memory, leave it empty — never fabricate. May also include preferences the user explicitly states in this request.",
      },
    },
    required: ["title", "duration_minutes", "invitees", "available_slots"],
  },
};

/** Tool handler function */
export function createInitiateMeetingHandler(apiClient: ClawMeetingApiClient) {
  return async (params: {
    title: string;
    duration_minutes: number;
    invitees: string[];
    available_slots: string[];
    preference_note?: string;
  }) => {
    const {
      title,
      duration_minutes,
      invitees,
      available_slots,
      preference_note,
    } = params;

    // 1. Check Token
    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "Identity not bound yet. Please call bind_identity first.",
      };
    }

    // 2. Validate required params
    if (!available_slots || available_slots.length === 0) {
      return {
        success: false,
        message: "Missing available_slots. Please parse from the user's description.",
      };
    }
    if (!duration_minutes || duration_minutes <= 0) {
      return {
        success: false,
        message: "Missing or invalid duration_minutes. Please parse from the user's description.",
      };
    }

    // 3. preference_note filled by Agent from user memory
    const finalNote = preference_note ?? undefined;

    // 4. Build request
    const requestData: InitiateMeetingRequest = {
      title,
      duration_minutes,
      invitees,
      initiator_data: {
        available_slots,
        preference_note: finalNote,
      },
    };

    // 5. Call API
    try {
      const result = await apiClient.initiateMeeting(requestData);

      // 6. Validate response fields
      const errors: string[] = [];
      if (!result.id || typeof result.id !== "string") {
        errors.push("Response missing 'id' field or invalid format");
      }
      if (!result.status) {
        errors.push("Response missing 'status' field");
      } else if (result.status !== "COLLECTING") {
        errors.push(`Unexpected status: expected COLLECTING, got ${result.status}`);
      }
      if (!result.title || typeof result.title !== "string") {
        errors.push("Response missing 'title' field");
      }
      if (!result.duration_minutes || result.duration_minutes <= 0) {
        errors.push("Response missing or invalid 'duration_minutes'");
      }
      if (!result.initiator_data) {
        errors.push("Response missing 'initiator_data' field");
      } else {
        if (!result.initiator_data.available_slots || !Array.isArray(result.initiator_data.available_slots) || result.initiator_data.available_slots.length === 0) {
          errors.push("initiator_data.available_slots is empty or invalid");
        }
      }
      if (!result.invitees || !Array.isArray(result.invitees) || result.invitees.length === 0) {
        errors.push("Response missing 'invitees' or empty array");
      }

      if (errors.length > 0) {
        console.log(`[ClawMeeting] initiate_meeting response validation warnings: ${errors.join("; ")}`);
        return {
          success: false,
          message: `Meeting creation request sent but response validation failed: ${errors.join("; ")}`,
          raw_response: result,
        };
      }

      return {
        success: true,
        message: "Meeting negotiation initiated!",
        meeting_id: result.id,
        title: result.title,
        status: result.status,
        details: {
          title,
          duration_minutes,
          invitees,
          available_slots_count: available_slots.length,
          available_slots,
          preference_note: finalNote,
        },
        next_step:
          "The server will notify invitees' OpenClaw plugins. They will submit their availability. You can check progress later via polling.",
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Failed to initiate meeting: ${errMsg}`,
        hint: "Please verify the server is running and all invitee emails are valid.",
      };
    }
  };
}
