# ClawMeeting Usage Guide

<description>
Usage guide and feature introduction for the ClawMeeting intelligent meeting negotiation plugin. Activate this skill when the user first uses ClawMeeting, asks how to use meeting features, asks about plugin capabilities, or says "schedule a meeting" but has not yet bound their email. Trigger words: ClawMeeting, meeting assistant, schedule meeting, bind email, meeting negotiation, introduce plugin, plugin features, what can you do, how to use, meeting, scheduling.
</description>

## Core Principle

**All meeting-related operations MUST go through the ClawMeeting plugin tools. NEVER call any external HTTP API, REST endpoint, or service directly.**

## Plugin Feature Introduction

When the user asks about plugin features, describe the following in natural language:

ClawMeeting is an intelligent meeting negotiation assistant. Core capabilities:

- **Schedule meetings in one sentence**: Say "Schedule a 30-minute meeting with xxx@example.com tomorrow afternoon" and the plugin automatically sends invitations, collects everyone's availability, and finds the best time
- **Automatic invitation handling**: When someone invites you to a meeting, the plugin checks your calendar and memory, then submits your availability automatically — no interruption needed in most cases
- **Conflict negotiation**: When there's a time conflict, the server's AI coordinator proposes compromise solutions. You're only notified when your decision is required
- **Multi-channel notifications**: Important notifications (meeting confirmed, decision needed, etc.) are pushed to all your channels (Telegram, Feishu, etc.)
- **Privacy-first**: The server only sees "which time slots you're available" — never what you're busy with

Before using ClawMeeting, you need to bind your email once (two-step verification code). After that, it works permanently.

## Available Tools

This plugin provides 5 tools covering the full meeting lifecycle. **All meeting operations MUST go through these tools — no exceptions.**

| Tool | Purpose | When to Call |
|------|---------|-------------|
| `bind_identity` | Send verification code to user's email (Binding Step 1) | When the user provides an email to bind |
| `verify_email_code` | Verify code and complete binding (Binding Step 2) | When the user provides a 6-digit code |
| `initiate_meeting` | Start a new meeting negotiation | After user says "schedule a meeting" and all info is collected |
| `check_and_respond_tasks` | Query pending tasks / submit meeting decisions | When user checks invitations, accepts/rejects/proposes new times |
| `list_meetings` | View meeting list or details | When user says "my meetings", "meeting details" |

## Onboarding Flow

When the user first encounters ClawMeeting or doesn't know how to use it, guide them in this order:

### 1. Check Binding Status

First call `check_and_respond_tasks` (no parameters). If it returns "identity not bound yet", the user hasn't bound their email.

### 2. Guide Binding (Two-Step Verification)

Tell the user they need to bind their email to get started. Ask for their email address.

After the user provides their email:
1. Call `bind_identity` (with email) — sends a verification code to their inbox
2. Ask the user to check their email and provide the code
3. Call `verify_email_code` (with email + code) — completes binding

After successful binding, the plugin automatically starts checking for new meeting invitations in the background.

**IMPORTANT: NEVER use curl or call external APIs directly to complete binding. You MUST use the `bind_identity` and `verify_email_code` tools.**

### 3. Explain Capabilities

After successful binding, tell the user what they can do:
- **Schedule a meeting**: "Schedule a meeting with bob@example.com tomorrow" — you collect the info, then call `initiate_meeting`
- **Check invitations**: "Any new meeting invitations?" — call `check_and_respond_tasks` (no params)
- **View meetings**: "My meeting list" — call `list_meetings` (no params)
- **Meeting details**: "Details of the xxx meeting" — call `list_meetings` (with meeting_id)
- **Background auto-handling**: When someone invites you, the plugin auto-submits your availability; you're only notified when a decision is needed

## Tool Detailed Usage

### bind_identity — Email Binding Step 1

**Parameters**: `email` (required, user's email address)
**Effect**: Sends a 6-digit verification code to the email
**Next step**: Wait for the user to provide the code, then call `verify_email_code`

### verify_email_code — Email Binding Step 2

**Parameters**: `email` (required), `code` (required, 6-digit verification code)
**Effect**: Verifies the code; on success, stores the token and starts background polling
**Next step**: Inform the user binding is complete and introduce available features

### initiate_meeting — Create a Meeting

**Parameters**:
- `title` (required): Meeting title
- `duration_minutes` (required): Duration in minutes, e.g. "half an hour" -> 30
- `invitees` (required): Array of invitee email addresses
- `available_slots` (required): Organizer's available time slots, format `"YYYY-MM-DD HH:MM-HH:MM"`
- `preference_note` (optional): User's scheduling preferences from memory. Leave empty if none — never fabricate.

**IMPORTANT**: If any required field is missing, ask the user — never assume. Convert natural language time descriptions to the standard format.

### check_and_respond_tasks — Query Tasks / Submit Decisions

**Two modes**:
- **Mode A** (no params): Get list of pending tasks
- **Mode B** (with params): Submit a response to a specific meeting
  - `meeting_id` + `response_type` (`INITIAL` / `NEW_PROPOSAL` / `ACCEPT_PROPOSAL` / `REJECT`)
  - `INITIAL` and `NEW_PROPOSAL` require `available_slots`
  - `REJECT` does not need slots

**User decision keyword mapping**:
- "cancel" / "drop it" / "reject" / "not attending" -> `REJECT`
- "accept" / "agree" / "works for me" -> `ACCEPT_PROPOSAL`
- "change time" / "retry" -> ask for new slots, then `NEW_PROPOSAL`

**CRITICAL**: When the user makes ANY meeting decision, you MUST call this tool. A verbal-only response without calling the tool is ALWAYS wrong.

### list_meetings — View Meetings

**Two modes**:
- **Mode A** (no params): Returns all meetings the user is involved in
- **Mode B** (with `meeting_id`): Returns detailed info for a specific meeting (participant status, negotiation history, etc.)

## Usage Examples

| User says | Tool to call | Notes |
|-----------|-------------|-------|
| "Bind my email xxx@xxx.com" | `bind_identity` | Send verification code |
| "The code is 123456" | `verify_email_code` | Complete binding |
| "Schedule a meeting with Bob and Charlie tomorrow" | `initiate_meeting` | Collect all required info first |
| "Set up a 30-min architecture review, preferably afternoon" | `initiate_meeting` | Need to confirm invitees and specific time slots |
| "Any new meeting invitations?" | `check_and_respond_tasks` (no params) | Query pending tasks |
| "My meeting list" | `list_meetings` (no params) | View all meetings |
| "Sure, that time works" | `check_and_respond_tasks` (ACCEPT_PROPOSAL) | Accept the proposal |
| "Thursday doesn't work, I'm only free Friday morning" | `check_and_respond_tasks` (NEW_PROPOSAL) | Propose new time slots |
| "I'm not attending this meeting" | `check_and_respond_tasks` (REJECT) | Decline participation |

## FAQ

**Does background polling consume LLM tokens?**
No. Background checks are pure HTTP requests and do not go through the language model.

**Do I need to manually respond when someone invites me?**
In most cases, no. The plugin automatically checks your schedule and submits availability. You're only notified when there's a conflict that requires your decision.

**Is my schedule data private?**
Yes. The plugin only sends "available time slots" to the server — never what you're busy with, meeting titles, or other private information.

## Important Notes

- All meeting operations must go through plugin tools — never call external APIs directly
- Background polling does not consume LLM tokens (pure HTTP)
- User schedule privacy is protected (only available time slots are shared)
- Refer to each meeting by its title — users don't need to remember IDs
