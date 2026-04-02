# ClawMeeting — AI Meeting Negotiation Plugin for OpenClaw

An OpenClaw plugin that turns your AI agent into a meeting scheduling assistant. It connects to a central coordination server, automatically collects availability from all participants, and negotiates the best meeting time — all through natural conversation.

## How It Works

```
You ←→ AI Agent ←→ ClawMeeting Plugin ←→ Coordination Server ←→ Other Participants' Agents
```

1. **You say** "Schedule a meeting with bob@example.com tomorrow afternoon"
2. **Your agent** parses the request and calls the plugin
3. **The plugin** sends it to the coordination server
4. **The server** notifies all invitees' plugins to collect their availability
5. **An AI coordinator** on the server finds the best time (or negotiates if there's a conflict)
6. **You get notified** when the meeting is confirmed

The whole process runs in the background. You only need to intervene when there's a conflict that requires your decision.

## Features

- **Natural language scheduling** — "Book a 30-min standup with Alice and Bob tomorrow 2-5pm"
- **Automatic invitation handling** — When someone invites you, your agent checks your calendar and memory, then submits availability automatically
- **Multi-round negotiation** — If times conflict, the server's AI coordinator proposes compromises; your agent presents them to you for approval
- **Privacy-first** — Only your available time slots are shared; the server never sees what you're busy with
- **Background polling** — Checks for new tasks every 10 seconds (pure HTTP, zero LLM token cost)
- **Deduplication** — Three-layer dedup system prevents duplicate notifications across restarts

## Install

```bash
openclaw plugins install clawmeeting
```

Or add to your config manually:

```json5
{
  plugins: {
    entries: {
      clawmeeting: {
        enabled: true,
        config: {
          serverUrl: "https://memcontext.ai/clawmeeting_api",  // coordination server
          pollingIntervalMs: 10000,                // poll every 10s
          autoRespond: true                        // enable background polling
        }
      }
    }
  }
}
```

Then restart: `openclaw gateway restart`

## Quick Start

### 1. Bind your email

```
You: Bind my email alice@company.com
```

This registers your identity with the coordination server. Only needed once — credentials persist across restarts.

### 2. Schedule a meeting

```
You: Schedule a 1-hour project review with bob@company.com and charlie@company.com tomorrow 2-5pm
```

The agent parses title, duration, invitees, and your available slots, then sends the request.

### 3. That's it

The plugin handles the rest in the background:
- ✅ Time match found → you get a confirmation with meeting details
- 🔄 Conflict detected → the coordinator proposes a compromise, your agent asks you to accept/reject/counter
- ❌ Negotiation failed → your agent asks if you want to cancel or try different times

## Tools (5)

These are the functions your AI agent can call:

| Tool | Purpose | Server API |
|------|---------|------------|
| `bind_identity` | Send verification code to email (Step 1) | POST /api/auth/send-code |
| `verify_email_code` | Verify code and complete binding (Step 2) | POST /api/auth/verify-bind |
| `initiate_meeting` | Start a new meeting negotiation | POST /api/meetings |
| `check_and_respond_tasks` | View pending tasks / submit responses | GET /api/tasks/pending, POST /api/meetings/{id}/submit |
| `list_meetings` | View meeting list or details | GET /api/meetings, GET /api/meetings/{id} |

### `check_and_respond_tasks` response types

| Type | When | Needs slots? |
|------|------|-------------|
| `INITIAL` | First-time availability submission | Yes |
| `NEW_PROPOSAL` | Counter-propose during negotiation | Yes |
| `ACCEPT_PROPOSAL` | Accept the coordinator's suggestion | No |
| `REJECT` | Decline participation | No |

> **FAILED retry**: When negotiation fails, the initiator can restart with `response_type='INITIAL'` plus new `available_slots`. Optionally pass `duration_minutes` to change the meeting length or `invitees` to add/remove participants.

### `initiate_meeting` errors

| Code | Meaning |
|------|---------|
| `403` | Initiator's email not verified — complete two-step binding first |
| `400` + `unregistered_emails` | One or more invitees are not registered; the field lists the specific addresses |

## Architecture

```
┌────────────────────── Gateway Process (Node.js) ──────────────────────┐
│                                                                       │
│  ┌─────────────┐         ┌──────────────────────────────────┐        │
│  │  Framework   │         │        ClawMeeting Plugin         │        │
│  │             │         │                                  │        │
│  │  Agent      │◄────────│  5 Tools (LLM-callable)          │        │
│  │  Session    │         │  PollingManager (every 10s)       │        │
│  │  Gateway    │◄────────│  sessions_send + message tool     │        │
│  │  Prompt     │◄────────│  before_prompt_build hook         │        │
│  └─────────────┘         └──────────┬───────────────────────┘        │
│                                     │ HTTP                           │
└─────────────────────────────────────┼────────────────────────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │  Coordination Server      │
                         │  • Meeting lifecycle      │
                         │  • AI time coordination   │
                         │  • Cross-user messaging   │
                         └──────────────────────────┘
```

### Notification flow (plugin → agent → channels)

```
Polling discovers new task
  → sessions_send to main session (agent processes silently)
  → Extract agent reply from HTTP response (result.details.reply)
  → message tool pushes reply to all extra channels (Telegram/Feishu/Discord)
  → Fallback: if reply extraction fails, push buildDirectNotification text
  → Fallback: if sessions_send fails entirely, queue for prependContext injection
```

Webchat users see the agent's response directly. Extra channel users receive the same reply via message tool (`action: "send"`).

### Deduplication (3 layers)

| Set | Purpose | Persisted? |
|-----|---------|-----------|
| `notifiedMeetings` | CONFIRMED/OVER — notify once | ✅ Disk |
| `submittedMeetings` | INITIAL_SUBMIT — don't re-submit | ❌ Memory only |
| `pendingDecisions` | COUNTER_PROPOSAL/FAILED — don't re-notify while waiting | ✅ Disk |

### Local storage

```
~/.openclaw/clawmeeting/
  ├── credentials.json        ← { email, token, user_id }
  ├── session.json            ← { sessionKey, channel } (main webchat session)
  ├── channel-{name}.json     ← { sessionKey, channel } (e.g. channel-telegram.json)
  ├── notified-meetings.json  ← ["mtg_xxx", ...]
  └── pending-decisions.json  ← ["mtg_yyy", ...]
```

## Meeting Lifecycle

```
PENDING → COLLECTING → ANALYZING → CONFIRMED → OVER
                                 → NEGOTIATING → CONFIRMED / FAILED
```

| Status | Meaning |
|--------|---------|
| PENDING | Meeting created, waiting for initiator's first submission |
| COLLECTING | Waiting for all participants to submit availability |
| ANALYZING | AI coordinator is finding the best time |
| CONFIRMED | Meeting time finalized |
| NEGOTIATING | Time conflict — coordinator sent compromise proposals |
| FAILED | Could not find a mutually agreeable time |
| OVER | Confirmed meeting has ended and been archived |

## Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `serverUrl` | string | `https://memcontext.ai/clawmeeting_api` | Coordination server URL |
| `pollingIntervalMs` | number | `10000` | Background polling interval (ms) |
| `autoRespond` | boolean | `true` | Enable background polling and auto-notification |

## CLI

```bash
openclaw clawmeeting-status
```

Shows server URL, polling status, bound email, and notification stats.

## File Structure

```
clawmeeting/
├── index.ts                    # Plugin entry: register, polling, hooks, tools
├── package.json
├── openclaw.plugin.json        # Plugin manifest
├── README.md                   # This file
├── skills/
│   └── clawmeeting-guide/
│       └── SKILL.md            # Agent skill for onboarding
└── src/
    ├── tools/
    │   ├── bind-identity.ts            # Tool: send verification code (Step 1)
    │   ├── verify-email-code.ts        # Tool: verify code + bind (Step 2)
    │   ├── initiate-meeting.ts         # Tool: create meeting
    │   ├── check-and-respond-tasks.ts  # Tool: poll tasks + submit responses
    │   └── list-meetings.ts            # Tool: view meetings
    ├── types/
    │   └── index.ts                    # TypeScript type definitions
    └── utils/
        ├── api-client.ts               # HTTP client for coordination server
        ├── polling-manager.ts           # Background polling with concurrency guard
        └── storage.ts                   # Local persistence (credentials, session, dedup)
```

## License

MIT
