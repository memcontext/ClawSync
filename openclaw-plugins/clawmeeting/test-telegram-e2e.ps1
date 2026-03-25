# ClawMeeting Telegram E2E Test
# Usage: powershell -File test-telegram-e2e.ps1
#
# 测试目标：
#   Phase 1: 发起会议邀请 runfengsun@gmail.com → 验证 Telegram 收到推送 → 等插件自动提交 → CONFIRMED
#   Phase 2: 用 Phase1 确认的时间段再发一个会议 → 时间冲突 → 协商阶段 → 最终 FAILED
#
# 组织者: 2226957164@qq.com (token from 1.md)
# 受邀者: runfengsun@gmail.com (通过 OpenClaw Telegram bot 接收通知)

$serverUrl = "http://39.105.143.2:7010"
$tokenInitiator = "sk-UUw_9i3qeZSA5x7wbf22b6cc6bc27f56"
$inviteeEmail = "uppxxcco@gmail.com"
$passCount = 0
$failCount = 0

# ============================================================
#  Helper Functions
# ============================================================

function Assert-Check {
    param([string]$Name, [bool]$Condition)
    if ($Condition) {
        Write-Host "  [PASS] $Name" -ForegroundColor Green
        $script:passCount++
    } else {
        Write-Host "  [FAIL] $Name" -ForegroundColor Red
        $script:failCount++
    }
}

function Get-Token {
    param([string]$Email)
    $res = Invoke-RestMethod -Uri "$serverUrl/api/auth/bind" -Method POST -ContentType "application/json" -Body "{`"email`":`"$Email`"}"
    return $res.data.token
}

function New-Meeting {
    param([string]$Token, [string]$Title, [int]$Duration, [string[]]$Invitees, [string[]]$Slots)
    $headers = @{ "Authorization" = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
    $body = [System.Text.Encoding]::UTF8.GetBytes((@{
        title = $Title
        duration_minutes = $Duration
        invitees = $Invitees
        initiator_data = @{ available_slots = $Slots }
    } | ConvertTo-Json -Depth 5))
    $res = Invoke-RestMethod -Uri "$serverUrl/api/meetings" -Method POST -Headers $headers -Body $body
    return $res
}

function Submit-Response {
    param([string]$Token, [string]$MeetingId, [string]$Type, [string[]]$Slots, [string]$Note)
    $headers = @{ "Authorization" = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
    $bodyObj = @{ response_type = $Type }
    if ($Slots) { $bodyObj.available_slots = $Slots }
    if ($Note) { $bodyObj.preference_note = $Note }
    $body = [System.Text.Encoding]::UTF8.GetBytes(($bodyObj | ConvertTo-Json -Depth 5))
    $res = Invoke-RestMethod -Uri "$serverUrl/api/meetings/$MeetingId/submit" -Method POST -Headers $headers -Body $body
    return $res
}

function Get-PendingTasks {
    param([string]$Token)
    $headers = @{ "Authorization" = "Bearer $Token" }
    $res = Invoke-RestMethod -Uri "$serverUrl/api/tasks/pending" -Method GET -Headers $headers
    return $res.data.pending_tasks
}

function Get-MeetingDetail {
    param([string]$Token, [string]$MeetingId)
    $headers = @{ "Authorization" = "Bearer $Token" }
    $res = Invoke-RestMethod -Uri "$serverUrl/api/meetings/$MeetingId" -Method GET -Headers $headers
    return $res.data
}

function Wait-MeetingStatus {
    param([string]$Token, [string]$MeetingId, [string[]]$TargetStatuses, [int]$MaxWait = 120)
    Write-Host "  Waiting for status [$($TargetStatuses -join '/')] (max ${MaxWait}s)..." -ForegroundColor Yellow
    $detail = $null
    for ($i = 0; $i -lt $MaxWait; $i += 5) {
        Start-Sleep -Seconds 5
        $detail = Get-MeetingDetail -Token $Token -MeetingId $MeetingId
        Write-Host "  ... $($detail.status) (${i}s)" -ForegroundColor Gray
        if ($detail.status -in $TargetStatuses) {
            Write-Host "  -> Status: $($detail.status) (${i}s)" -ForegroundColor Cyan
            return $detail
        }
    }
    Write-Host "  -> Timeout! Current status: $($detail.status)" -ForegroundColor Red
    return $detail
}

function Pause-WithPrompt {
    param([string]$Message)
    Write-Host ""
    Write-Host "  >> $Message" -ForegroundColor Yellow
    Write-Host "  >> Press ENTER to continue..." -ForegroundColor Yellow
    Read-Host
}

# ============================================================
#  Setup
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  ClawMeeting Telegram E2E Test" -ForegroundColor White
Write-Host "  Initiator: 2226957164@qq.com" -ForegroundColor Gray
Write-Host "  Invitee:   $inviteeEmail (Telegram)" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup: Get invitee token ---" -ForegroundColor Cyan
$tokenInvitee = Get-Token -Email $inviteeEmail
Write-Host "  Initiator token: $($tokenInitiator.Substring(0,20))..."
Write-Host "  Invitee token:   $($tokenInvitee.Substring(0,20))..."

# ============================================================
#  Phase 1: Normal Accept -> CONFIRMED
#  目的: 验证 Telegram 能收到会议邀请推送
#        插件自动提交后 Agent 确认 → 双方收到 CONFIRMED
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Phase 1: Normal Meeting -> CONFIRMED" -ForegroundColor White
Write-Host "  (Telegram push notification test)" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor White

# P1-1: Create meeting - 使用明天下午的时间段
$tomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
$slot1 = "$tomorrow 14:00-16:00"
Write-Host "`n[P1-1] Create meeting (slot: $slot1)" -ForegroundColor Cyan
$res1 = New-Meeting -Token $tokenInitiator -Title "Telegram-Test-Accept" -Duration 30 -Invitees @($inviteeEmail) -Slots @($slot1)
$mid1 = $res1.data.id
Write-Host "  Meeting: $mid1  Status: $($res1.data.status)"
Assert-Check "Phase1 meeting created (COLLECTING)" ($res1.data.status -eq "COLLECTING")

Pause-WithPrompt "Meeting created. Check Telegram for invite notification from the bot."

# P1-2: Verify invitee has INITIAL_SUBMIT task
Write-Host "[P1-2] Check invitee pending tasks" -ForegroundColor Cyan
$tasks1 = Get-PendingTasks -Token $tokenInvitee
$inviteTask1 = $tasks1 | Where-Object { $_.meeting_id -eq $mid1 -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Invitee has INITIAL_SUBMIT task" ($null -ne $inviteTask1)

# P1-3: Wait for plugin auto-submit and Agent confirmation
Write-Host "`n[P1-3] Wait for plugin auto-submit + Agent -> CONFIRMED" -ForegroundColor Cyan
Write-Host "  (Plugin polls -> detects INITIAL_SUBMIT -> Agent checks calendar/memory -> submits -> Agent confirms)" -ForegroundColor Gray
$detail1 = Wait-MeetingStatus -Token $tokenInitiator -MeetingId $mid1 -TargetStatuses @("CONFIRMED", "FAILED") -MaxWait 120

if ($detail1.status -eq "CONFIRMED") {
    Write-Host "  Final time: $($detail1.final_time)" -ForegroundColor Green
    Assert-Check "Phase1 meeting CONFIRMED" $true

    Pause-WithPrompt "Phase1 CONFIRMED. Check Telegram for confirmation notification."

    # P1-4: Verify both parties get CONFIRMED notification
    Write-Host "[P1-4] Check notifications" -ForegroundColor Cyan
    $tasksInit1 = Get-PendingTasks -Token $tokenInitiator
    $confirmInit = $tasksInit1 | Where-Object { $_.meeting_id -eq $mid1 -and $_.task_type -eq "MEETING_CONFIRMED" }
    Assert-Check "Initiator received MEETING_CONFIRMED" ($null -ne $confirmInit)

    $tasksInv1 = Get-PendingTasks -Token $tokenInvitee
    $confirmInv = $tasksInv1 | Where-Object { $_.meeting_id -eq $mid1 -and $_.task_type -eq "MEETING_CONFIRMED" }
    Assert-Check "Invitee received MEETING_CONFIRMED" ($null -ne $confirmInv)

} elseif ($detail1.status -eq "COLLECTING") {
    Write-Host "  Still COLLECTING - plugin may not have auto-submitted yet" -ForegroundColor Yellow
    Assert-Check "Phase1 meeting progressed beyond COLLECTING" $false
} else {
    Write-Host "  Unexpected status: $($detail1.status)" -ForegroundColor Red
    Assert-Check "Phase1 meeting CONFIRMED" $false
}

# ============================================================
#  Phase 2: Overlapping Time -> Negotiation -> FAILED
#  目的: 用和 Phase1 完全重叠的时间发起新会议
#        受邀者（插件）应该发现冲突，提交不同时间 → 协商
#        组织者拒绝协商 → FAILED
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Phase 2: Overlapping Time -> Negotiation -> FAILED" -ForegroundColor White
Write-Host "  (Same slot as Phase1 to force conflict)" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor White

# P2-1: Create meeting with SAME time slot as Phase1
$slot2 = $slot1  # 故意用相同时间段，制造冲突
Write-Host "`n[P2-1] Create meeting with overlapping slot: $slot2" -ForegroundColor Cyan
$res2 = New-Meeting -Token $tokenInitiator -Title "Telegram-Test-Conflict" -Duration 30 -Invitees @($inviteeEmail) -Slots @($slot2)
$mid2 = $res2.data.id
Write-Host "  Meeting: $mid2  Status: $($res2.data.status)"
Assert-Check "Phase2 meeting created (COLLECTING)" ($res2.data.status -eq "COLLECTING")

Pause-WithPrompt "Phase2 meeting created (overlapping time). Check Telegram for invite."

# P2-2: Wait for plugin to auto-submit (may submit different time due to conflict)
Write-Host "[P2-2] Wait for plugin auto-submit + Agent analysis" -ForegroundColor Cyan
Write-Host "  (Plugin should detect Phase1 conflict -> submit different/no time -> Agent decides)" -ForegroundColor Gray
# 自定义等待: 等 CONFIRMED/FAILED，或 COLLECTING+round_count>=1 (协商回退)
Write-Host "  Waiting for status change (max 120s)..." -ForegroundColor Yellow
$detail2 = $null
for ($i = 0; $i -lt 120; $i += 5) {
    Start-Sleep -Seconds 5
    $detail2 = Get-MeetingDetail -Token $tokenInitiator -MeetingId $mid2
    Write-Host "  ... $($detail2.status) round=$($detail2.round_count) (${i}s)" -ForegroundColor Gray
    if ($detail2.status -in @("CONFIRMED", "FAILED")) { break }
    if ($detail2.status -eq "COLLECTING" -and $detail2.round_count -ge 1) { break }
}
Write-Host "  -> Status: $($detail2.status) round=$($detail2.round_count)" -ForegroundColor Cyan

if ($detail2.status -eq "COLLECTING" -and $detail2.round_count -ge 1) {
    # Agent decided to negotiate
    Write-Host "  Agent triggered negotiation (round $($detail2.round_count))" -ForegroundColor Yellow
    Assert-Check "Phase2 entered negotiation" $true

    Pause-WithPrompt "Negotiation started. Check Telegram for counter-proposal notification."

    # P2-3: Check COUNTER_PROPOSAL
    Write-Host "[P2-3] Check invitee has COUNTER_PROPOSAL" -ForegroundColor Cyan
    $tasks2 = Get-PendingTasks -Token $tokenInvitee
    $counterTask = $tasks2 | Where-Object { $_.meeting_id -eq $mid2 -and $_.task_type -eq "COUNTER_PROPOSAL" }
    Assert-Check "Invitee received COUNTER_PROPOSAL" ($null -ne $counterTask)
    if ($counterTask) {
        Write-Host "  Counter message: $($counterTask.message)" -ForegroundColor Gray
    }

    # P2-4: Reject the proposal to force FAILED
    Write-Host "`n[P2-4] Invitee rejects counter-proposal -> force FAILED" -ForegroundColor Cyan
    $submitReject = Submit-Response -Token $tokenInvitee -MeetingId $mid2 -Type "REJECT" -Note "Cannot make any of these times"
    Assert-Check "Reject submitted" ($submitReject.code -eq 200)

    # P2-5: Wait for FAILED
    Write-Host "`n[P2-5] Wait for FAILED status" -ForegroundColor Cyan
    $detail2f = Wait-MeetingStatus -Token $tokenInitiator -MeetingId $mid2 -TargetStatuses @("FAILED") -MaxWait 60
    Assert-Check "Phase2 meeting FAILED" ($detail2f.status -eq "FAILED")

    Pause-WithPrompt "Phase2 FAILED. Check Telegram for failure notification."

} elseif ($detail2.status -eq "CONFIRMED") {
    # Agent found overlap OK and confirmed - need manual reject
    Write-Host "  Agent confirmed directly (partial overlap accepted)" -ForegroundColor Yellow
    Write-Host "  This means the plugin didn't detect a conflict - proceeding to manual reject test" -ForegroundColor Yellow
    Assert-Check "Phase2 entered negotiation (got CONFIRMED instead)" $false

    # Fallback: create a 3rd meeting and manually reject to test FAILED path
    Write-Host "`n[P2-Fallback] Creating a 3rd meeting and manually rejecting" -ForegroundColor Cyan
    $slot3 = "$tomorrow 09:00-10:00"
    $res3 = New-Meeting -Token $tokenInitiator -Title "Telegram-Test-Reject" -Duration 30 -Invitees @($inviteeEmail) -Slots @($slot3)
    $mid3 = $res3.data.id
    Write-Host "  Meeting: $mid3  Status: $($res3.data.status)"

    Pause-WithPrompt "Fallback meeting created. Press ENTER after Telegram gets the invite."

    # Reject directly
    Write-Host "  Invitee rejects directly" -ForegroundColor Cyan
    $submitReject3 = Submit-Response -Token $tokenInvitee -MeetingId $mid3 -Type "REJECT" -Note "Not available at all"
    Assert-Check "Fallback reject submitted" ($submitReject3.code -eq 200)

    $detail3 = Wait-MeetingStatus -Token $tokenInitiator -MeetingId $mid3 -TargetStatuses @("FAILED") -MaxWait 60
    Assert-Check "Fallback meeting FAILED" ($detail3.status -eq "FAILED")

    Pause-WithPrompt "Fallback FAILED. Check Telegram for failure notification."

    # Check initiator gets FAILED notification
    Write-Host "  Check initiator FAILED notification" -ForegroundColor Cyan
    $tasksFail = Get-PendingTasks -Token $tokenInitiator
    $failTask = $tasksFail | Where-Object { $_.meeting_id -eq $mid3 -and $_.task_type -eq "MEETING_FAILED" }
    Assert-Check "Initiator received MEETING_FAILED" ($null -ne $failTask)

} elseif ($detail2.status -eq "FAILED") {
    Write-Host "  Agent decided FAILED directly (no overlap)" -ForegroundColor Yellow
    Assert-Check "Phase2 meeting FAILED" $true

    Pause-WithPrompt "Phase2 FAILED directly. Check Telegram for failure notification."

} else {
    Write-Host "  Still COLLECTING after timeout" -ForegroundColor Red
    Assert-Check "Phase2 progressed beyond COLLECTING" $false
}

# ============================================================
#  Final: Check initiator FAILED notifications
# ============================================================

Write-Host "`n[Final] Check initiator FAILED notifications" -ForegroundColor Cyan
$tasksFinal = Get-PendingTasks -Token $tokenInitiator
$failTasks = $tasksFinal | Where-Object { $_.task_type -eq "MEETING_FAILED" }
Write-Host "  Initiator has $(@($failTasks).Count) MEETING_FAILED notification(s)" -ForegroundColor Gray

# ============================================================
#  Summary
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Telegram E2E Test Summary" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host "  PASS: $passCount" -ForegroundColor Green
Write-Host "  FAIL: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })
Write-Host "  Total: $($passCount + $failCount)" -ForegroundColor White
Write-Host ""

Write-Host "  Checklist (manual verification):" -ForegroundColor Yellow
Write-Host "    [ ] Telegram bot sent Phase1 invite notification" -ForegroundColor Yellow
Write-Host "    [ ] Telegram bot sent Phase1 CONFIRMED notification" -ForegroundColor Yellow
Write-Host "    [ ] Telegram bot sent Phase2 invite notification" -ForegroundColor Yellow
Write-Host "    [ ] Telegram bot sent Phase2 negotiation/failure notification" -ForegroundColor Yellow
Write-Host ""

if ($failCount -eq 0) {
    Write-Host "  All automated checks passed!" -ForegroundColor Green
} else {
    Write-Host "  Some checks failed. Review output above." -ForegroundColor Red
}
Write-Host ""
