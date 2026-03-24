# ClawMeeting Notification Test Script
# Usage: powershell -File test-notify.ps1
#
# Flow:
#   1. User 132 creates a meeting, invites 123
#   2. Wait 20s - check gateway logs for notification delivery
#   3. User 123 submits availability
#   4. Wait 20s - check gateway logs for confirmation notification
#
# Expected log output:
#   OK:  "[ClawMeeting] 推送成功 (sessions_send)" = delivered via sessions_send
#   OK:  "[ClawMeeting] 已放入 pendingNotifications 兜底" = queued for next user message
#   BUG: same notification appears twice = duplicate (needs fix)

$token132 = "sk-FVklq4YEBJ4GOy3z8d43b780bb9ef86d"
$serverUrl = "http://39.105.143.2:7010"

# --- Get 123 token ---
Write-Host "`n=== Step 0: Get token for 123 ===" -ForegroundColor Cyan
$bindRes = Invoke-RestMethod -Uri "$serverUrl/api/auth/bind" -Method POST -ContentType "application/json" -Body '{"email":"upp@123.com"}'
$token123 = $bindRes.data.token
Write-Host "123 token: $token123"

# --- Step 1: 132 creates meeting ---
Write-Host "`n=== Step 1: 132 creates meeting inviting 123 ===" -ForegroundColor Cyan
$headers132 = @{ "Authorization" = "Bearer $token132"; "Content-Type" = "application/json; charset=utf-8" }
$meetingBody = [System.Text.Encoding]::UTF8.GetBytes((@{
  title = "notification-test"
  duration_minutes = 30
  invitees = @("upp@123.com")
  initiator_data = @{
    available_slots = @("2026-03-29 10:00-12:00")
  }
} | ConvertTo-Json -Depth 5))

$createRes = Invoke-RestMethod -Uri "$serverUrl/api/meetings" -Method POST -Headers $headers132 -Body $meetingBody
$meetingId = $createRes.data.id
Write-Host "Meeting created: $meetingId  status: $($createRes.data.status)"

# --- Step 2: Wait for polling ---
Write-Host "`n=== Step 2: Waiting 20s for plugin polling ===" -ForegroundColor Yellow
Write-Host "Watch gateway logs for: [ClawMeeting] lines"
Start-Sleep -Seconds 20

# --- Step 3: Check 123 pending tasks ---
Write-Host "`n=== Step 3: Check 123 pending tasks ===" -ForegroundColor Cyan
$headers123 = @{ "Authorization" = "Bearer $token123"; "Content-Type" = "application/json" }
$tasksRes = Invoke-RestMethod -Uri "$serverUrl/api/tasks/pending" -Method GET -Headers $headers123
$taskCount = $tasksRes.data.pending_tasks.Count
Write-Host "123 pending tasks: $taskCount"
foreach ($t in $tasksRes.data.pending_tasks) {
  Write-Host "  - [$($t.task_type)] $($t.title) ($($t.meeting_id))"
}

# --- Step 4: 123 submits availability ---
Write-Host "`n=== Step 4: 123 submits availability ===" -ForegroundColor Cyan
$submitBody = @{
  response_type = "INITIAL"
  available_slots = @("2026-03-29 10:00-12:00")
} | ConvertTo-Json -Depth 5
$submitRes = Invoke-RestMethod -Uri "$serverUrl/api/meetings/$meetingId/submit" -Method POST -Headers $headers123 -Body $submitBody
Write-Host "Submit result: status=$($submitRes.data.status) all_submitted=$($submitRes.data.all_submitted)"

# --- Step 5: Wait for confirmation ---
Write-Host "`n=== Step 5: Waiting 20s for confirmation notification ===" -ForegroundColor Yellow
Write-Host "Watch gateway logs for: MEETING_CONFIRMED"
Start-Sleep -Seconds 20

# --- Step 6: Check final status ---
Write-Host "`n=== Step 6: Check meeting final status ===" -ForegroundColor Cyan
$detailRes = Invoke-RestMethod -Uri "$serverUrl/api/meetings/$meetingId" -Method GET -Headers $headers123
Write-Host "Status: $($detailRes.data.status)"
Write-Host "Time:   $($detailRes.data.final_time)"

Write-Host "`n=== Test complete ===" -ForegroundColor Green
Write-Host "Review gateway logs above to verify notification delivery"
