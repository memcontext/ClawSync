// ============================================================
// Tool 4: ListMeetingsTool (list_meetings)
// API 3 + API 4:
//   GET /api/meetings              — List my meetings
//   GET /api/meetings/{meeting_id} — Get meeting details
//
// User says: "show my meetings" / "meeting details" / "how's the xxx meeting going?"
// Agent calls this tool to view meeting list or specific meeting details
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";

/** Tool JSON Schema definition */
export const listMeetingsSchema = {
  name: "list_meetings",
  description: [
    "[ClawMeeting Plugin Tool] View the user's meeting list or details of a specific meeting.",
    "IMPORTANT: Always use this tool to view meetings — never call any external API directly.",
    "",
    "Mode A - No meeting_id: Returns all meetings the user is involved in (initiated and invited), sorted by creation time (newest first).",
    "Mode B - With meeting_id: Returns detailed info for that meeting, including participant status, coordinator reasoning, etc.",
    "",
    "The user might say:",
    "  - 'show my meetings'",
    "  - 'how's the xxx meeting going?'",
    "  - 'meeting details'",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      meeting_id: {
        type: "string" as const,
        description: "Meeting ID (optional). If omitted, returns the meeting list. If provided, returns that meeting's details.",
      },
    },
    required: [],
  },
};

/** Tool handler function */
export function createListMeetingsHandler(apiClient: ClawMeetingApiClient) {
  return async (params: { meeting_id?: string }) => {
    const { meeting_id } = params;

    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "Identity not bound yet. Please call bind_identity first.",
      };
    }

    // Mode B: Query specific meeting details
    if (meeting_id) {
      try {
        const detail = await apiClient.getMeetingDetail(meeting_id);
        return {
          success: true,
          meeting: detail,
        };
      } catch (error: unknown) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return {
          success: false,
          message: `Failed to fetch meeting details: ${errMsg}`,
        };
      }
    }

    // Mode A: Query meeting list
    try {
      const list = await apiClient.getMeetingList();
      return {
        success: true,
        total: list.total,
        meetings: list.meetings,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Failed to fetch meeting list: ${errMsg}`,
      };
    }
  };
}
