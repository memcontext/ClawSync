#!/bin/bash
# ClawMeeting End-to-End Test Script
# Tests the full automated flow: create meeting → plugin auto-submits → coordinator confirms → notification delivered
#
# Usage: bash test-notify.sh
#
# Prerequisites:
#   - API server running at SERVER_URL
#   - OpenClaw gateway running with clawmeeting plugin loaded
#   - runfengsun@gmail.com bound in plugin

TOKEN_132="sk-FVklq4YEBJ4GOy3z8d43b780bb9ef86d"
SERVER_URL="http://39.105.143.2:7010"
INVITEE_EMAIL="runfengsun@gmail.com"
MAX_WAIT=120  # max seconds to wait for each stage

# --- Step 0: Get invitee token ---
echo ""
echo "=== Step 0: Get token for $INVITEE_EMAIL ==="
BIND_RES=$(curl -s -X POST "$SERVER_URL/api/auth/bind" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$INVITEE_EMAIL\"}")
TOKEN_INVITEE=$(echo "$BIND_RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
echo "Invitee token: $TOKEN_INVITEE"

# --- Step 1: Create meeting ---
echo ""
echo "=== Step 1: Creating meeting (inviting $INVITEE_EMAIL) ==="
CREATE_RES=$(curl -s -X POST "$SERVER_URL/api/meetings" \
  -H "Authorization: Bearer $TOKEN_132" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"auto-test-$(date +%H%M%S)\",
    \"duration_minutes\": 30,
    \"invitees\": [\"$INVITEE_EMAIL\"],
    \"initiator_data\": {
      \"available_slots\": [\"2026-03-29 10:00-12:00\"]
    }
  }")
MEETING_ID=$(echo "$CREATE_RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
TITLE=$(echo "$CREATE_RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['title'])")
echo "Meeting: $TITLE ($MEETING_ID) → COLLECTING"

# --- Step 2: Wait for plugin to auto-submit (COLLECTING → ANALYZING) ---
echo ""
echo "=== Step 2: Waiting for plugin to auto-submit availability ==="
echo "  Plugin should: poll → detect INITIAL_SUBMIT → Agent checks calendar/memory → submit"
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(curl -s -X GET "$SERVER_URL/api/meetings/$MEETING_ID" \
    -H "Authorization: Bearer $TOKEN_INVITEE" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('status',''))" 2>/dev/null)

  if [ "$STATUS" != "COLLECTING" ]; then
    echo "  ✓ Status changed to: $STATUS (after ${ELAPSED}s)"
    break
  fi

  sleep 5
  ELAPSED=$((ELAPSED + 5))
  echo "  ... still COLLECTING (${ELAPSED}s)"
done

if [ "$STATUS" = "COLLECTING" ]; then
  echo "  ✗ TIMEOUT: Still COLLECTING after ${MAX_WAIT}s"
  echo "  Check: Is the plugin polling? Did Agent fail to submit?"

  # Show pending tasks for debugging
  echo ""
  echo "  Debug: Invitee pending tasks:"
  curl -s -X GET "$SERVER_URL/api/tasks/pending" \
    -H "Authorization: Bearer $TOKEN_INVITEE" | \
    python3 -c "
import sys, json
tasks = json.load(sys.stdin).get('data',{}).get('pending_tasks',[])
for t in tasks:
    if t['meeting_id'] == '$MEETING_ID':
        print(f'    [{t[\"task_type\"]}] {t[\"title\"]} - action_required')
" 2>/dev/null
  exit 1
fi

# --- Step 3: Wait for coordinator to process (ANALYZING → CONFIRMED/FAILED) ---
if [ "$STATUS" = "ANALYZING" ]; then
  echo ""
  echo "=== Step 3: Waiting for Coordinator Agent to analyze ==="
  ELAPSED=0
  while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(curl -s -X GET "$SERVER_URL/api/meetings/$MEETING_ID" \
      -H "Authorization: Bearer $TOKEN_INVITEE" | \
      python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('status',''))" 2>/dev/null)

    if [ "$STATUS" != "ANALYZING" ]; then
      echo "  ✓ Status changed to: $STATUS (after ${ELAPSED}s)"
      break
    fi

    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo "  ... still ANALYZING (${ELAPSED}s)"
  done

  if [ "$STATUS" = "ANALYZING" ]; then
    echo "  ✗ TIMEOUT: Still ANALYZING after ${MAX_WAIT}s"
    echo "  Check: Is the Coordinator Agent running?"
    exit 1
  fi
fi

# --- Step 4: Verify final result ---
echo ""
echo "=== Step 4: Final result ==="
DETAIL_RES=$(curl -s -X GET "$SERVER_URL/api/meetings/$MEETING_ID" \
  -H "Authorization: Bearer $TOKEN_INVITEE")
echo "$DETAIL_RES" | python3 -c "
import sys, json
data = json.load(sys.stdin).get('data', {})
status = data.get('status')
final_time = data.get('final_time')
reasoning = data.get('coordinator_reasoning', '')

print(f'Status:    {status}')
if final_time:
    print(f'Time:      {final_time}')
if reasoning:
    print(f'Reasoning: {reasoning[:100]}')

# Check participants
for p in data.get('participants', []):
    slots = ', '.join(p.get('latest_slots', [])[:3]) if p.get('latest_slots') else 'none'
    note = p.get('preference_note', '') or ''
    print(f'  {p[\"email\"]} ({p[\"role\"]}): slots=[{slots}] note={note[:50]}')
"

# --- Step 5: Check notification delivery ---
echo ""
echo "=== Step 5: Check notification delivery ==="
if [ "$STATUS" = "CONFIRMED" ]; then
  # Check if MEETING_CONFIRMED task exists for invitee
  NOTIF=$(curl -s -X GET "$SERVER_URL/api/tasks/pending" \
    -H "Authorization: Bearer $TOKEN_INVITEE" | \
    python3 -c "
import sys, json
tasks = json.load(sys.stdin).get('data',{}).get('pending_tasks',[])
confirmed = [t for t in tasks if t['meeting_id'] == '$MEETING_ID' and t['task_type'] == 'MEETING_CONFIRMED']
if confirmed:
    print('✓ MEETING_CONFIRMED task found in pending (notification should be pushed)')
else:
    print('✗ No MEETING_CONFIRMED task found — may have been consumed by plugin already')
" 2>/dev/null)
  echo "  $NOTIF"
fi

echo ""
echo "=== Test complete ==="
echo "Check OpenClaw gateway logs for [ClawMeeting] entries to verify push delivery"
