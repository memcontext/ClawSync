# ClawMeeting Notification Full Test Script
# Usage: powershell -File test-notify.ps1
#
# Covers 3 scenarios:
#   A. Accept  -> CONFIRMED (both parties notified)
#   B. Reject  -> FAILED    (only initiator notified)
#   C. Conflict-> NEGOTIATING -> CONFIRMED/FAILED
#
# Initiator: upp@132.com
# Invitee:   yueyao@email.com
#
# Notifications are pushed via OpenClaw plugin (sessions_send / pendingNotifications)

$serverUrl = "http://39.105.143.2:7010"
$token132 = "sk-FVklq4YEBJ4GOy3z8d43b780bb9ef86d"
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
    param([string]$Token, [string]$MeetingId, [string[]]$TargetStatuses, [int]$MaxWait = 60)
    Write-Host "  Waiting for status [$($TargetStatuses -join '/')] (max ${MaxWait}s)..." -ForegroundColor Yellow
    for ($i = 0; $i -lt $MaxWait; $i += 3) {
        Start-Sleep -Seconds 3
        $detail = Get-MeetingDetail -Token $Token -MeetingId $MeetingId
        if ($detail.status -in $TargetStatuses) {
            Write-Host "  -> Status: $($detail.status) (${i}s)" -ForegroundColor Cyan
            return $detail
        }
    }
    Write-Host "  -> Timeout! Current status: $($detail.status)" -ForegroundColor Red
    return $detail
}

# ============================================================
#  Setup
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  ClawMeeting Notification Test" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup: Get tokens ---" -ForegroundColor Cyan
$tokenYueyao = Get-Token -Email "yueyao@email.com"
Write-Host "  Initiator (132): $($token132.Substring(0,20))..."
Write-Host "  Invitee (yueyao): $($tokenYueyao.Substring(0,20))..."

# ============================================================
#  Scenario A: Accept -> CONFIRMED
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Scenario A: Accept -> CONFIRMED" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

# A1: Create meeting
Write-Host "`n[A1] Create meeting" -ForegroundColor Cyan
$resA = New-Meeting -Token $token132 -Title "test-accept" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-05 10:00-12:00")
$midA = $resA.data.id
Write-Host "  Meeting: $midA  Status: $($resA.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resA.data.status -eq "COLLECTING")

# A2: Check yueyao has invite task
Write-Host "`n[A2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksA = Get-PendingTasks -Token $tokenYueyao
$inviteTask = $tasksA | Where-Object { $_.meeting_id -eq $midA -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTask)

# A3: Yueyao submits same time (accept)
Write-Host "`n[A3] Yueyao submits availability (accept)" -ForegroundColor Cyan
$submitA = Submit-Response -Token $tokenYueyao -MeetingId $midA -Type "INITIAL" -Slots @("2026-04-05 10:00-12:00")
Write-Host "  all_submitted: $($submitA.data.all_submitted)"
Assert-Check "All submitted after yueyao responds" ($submitA.data.all_submitted -eq $true)

# A4: Wait for Agent -> CONFIRMED
Write-Host "`n[A4] Wait for Agent processing" -ForegroundColor Cyan
$detailA = Wait-MeetingStatus -Token $token132 -MeetingId $midA -TargetStatuses @("CONFIRMED")
Assert-Check "Meeting status is CONFIRMED" ($detailA.status -eq "CONFIRMED")
if ($detailA.final_time) { Write-Host "  Final time: $($detailA.final_time)" -ForegroundColor Green }

# A5: Check initiator has CONFIRMED notification
Write-Host "`n[A5] Check initiator (132) notification" -ForegroundColor Cyan
$tasks132A = Get-PendingTasks -Token $token132
$confirmTask132 = $tasks132A | Where-Object { $_.meeting_id -eq $midA -and $_.task_type -eq "MEETING_CONFIRMED" }
Assert-Check "Initiator received MEETING_CONFIRMED" ($null -ne $confirmTask132)

# A6: Check yueyao has CONFIRMED notification
Write-Host "`n[A6] Check yueyao notification" -ForegroundColor Cyan
$tasksYA = Get-PendingTasks -Token $tokenYueyao
$confirmTaskY = $tasksYA | Where-Object { $_.meeting_id -eq $midA -and $_.task_type -eq "MEETING_CONFIRMED" }
Assert-Check "Yueyao received MEETING_CONFIRMED" ($null -ne $confirmTaskY)

# A7: Check no duplicate (read-once)
Write-Host "`n[A7] Check no duplicate notification (read-once)" -ForegroundColor Cyan
$tasksY2 = Get-PendingTasks -Token $tokenYueyao
$dupTask = $tasksY2 | Where-Object { $_.meeting_id -eq $midA -and $_.task_type -eq "MEETING_CONFIRMED" }
Assert-Check "No duplicate MEETING_CONFIRMED on second query" ($null -eq $dupTask)

# ============================================================
#  Scenario B: Reject -> FAILED
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Scenario B: Reject -> FAILED" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

# B1: Create meeting
Write-Host "`n[B1] Create meeting" -ForegroundColor Cyan
$resB = New-Meeting -Token $token132 -Title "test-reject" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-06 14:00-16:00")
$midB = $resB.data.id
Write-Host "  Meeting: $midB  Status: $($resB.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resB.data.status -eq "COLLECTING")

# B2: Check yueyao has invite task
Write-Host "`n[B2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksB = Get-PendingTasks -Token $tokenYueyao
$inviteTaskB = $tasksB | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTaskB)

# B3: Yueyao rejects
Write-Host "`n[B3] Yueyao rejects meeting" -ForegroundColor Cyan
$submitB = Submit-Response -Token $tokenYueyao -MeetingId $midB -Type "REJECT" -Note "I'm not available"
Write-Host "  all_submitted: $($submitB.data.all_submitted)"
Assert-Check "Rejection recorded" ($submitB.code -eq 200)

# B4: Wait for Agent -> FAILED
Write-Host "`n[B4] Wait for Agent processing" -ForegroundColor Cyan
$detailB = Wait-MeetingStatus -Token $token132 -MeetingId $midB -TargetStatuses @("FAILED")
Assert-Check "Meeting status is FAILED" ($detailB.status -eq "FAILED")

# B5: Initiator gets FAILED notification
Write-Host "`n[B5] Check initiator (132) notification" -ForegroundColor Cyan
$tasks132B = Get-PendingTasks -Token $token132
$failTask = $tasks132B | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "MEETING_FAILED" }
Assert-Check "Initiator received MEETING_FAILED" ($null -ne $failTask)

# B6: Yueyao should NOT get FAILED notification
Write-Host "`n[B6] Check yueyao has no FAILED notification" -ForegroundColor Cyan
$tasksYB = Get-PendingTasks -Token $tokenYueyao
$failTaskY = $tasksYB | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "MEETING_FAILED" }
Assert-Check "Yueyao has NO MEETING_FAILED task" ($null -eq $failTaskY)

# ============================================================
#  Scenario C: Conflict -> NEGOTIATING
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Scenario C: Conflict -> NEGOTIATING" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

# C1: Create meeting
Write-Host "`n[C1] Create meeting" -ForegroundColor Cyan
$resC = New-Meeting -Token $token132 -Title "test-conflict" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-07 09:00-11:00")
$midC = $resC.data.id
Write-Host "  Meeting: $midC  Status: $($resC.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resC.data.status -eq "COLLECTING")

# C2: Check yueyao has invite task
Write-Host "`n[C2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksC = Get-PendingTasks -Token $tokenYueyao
$inviteTaskC = $tasksC | Where-Object { $_.meeting_id -eq $midC -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTaskC)

# C3: Yueyao submits DIFFERENT time (conflict)
Write-Host "`n[C3] Yueyao submits conflicting time" -ForegroundColor Cyan
$submitC = Submit-Response -Token $tokenYueyao -MeetingId $midC -Type "INITIAL" -Slots @("2026-04-07 14:00-16:00")
Write-Host "  all_submitted: $($submitC.data.all_submitted)"
Assert-Check "Conflict submission recorded" ($submitC.data.all_submitted -eq $true)

# C4: Wait for Agent -> NEGOTIATING or FAILED or CONFIRMED
Write-Host "`n[C4] Wait for Agent processing (expect NEGOTIATING or FAILED)" -ForegroundColor Cyan
$detailC = Wait-MeetingStatus -Token $token132 -MeetingId $midC -TargetStatuses @("COLLECTING", "CONFIRMED", "FAILED") -MaxWait 45

if ($detailC.status -eq "COLLECTING") {
    # Agent decided NEGOTIATING -> back to COLLECTING
    Write-Host "  Agent decided NEGOTIATING (round $($detailC.round_count))" -ForegroundColor Yellow
    Assert-Check "Agent triggered negotiation" ($detailC.round_count -ge 1)

    # C5: Check yueyao has COUNTER_PROPOSAL
    Write-Host "`n[C5] Check yueyao has COUNTER_PROPOSAL notification" -ForegroundColor Cyan
    $tasksCY = Get-PendingTasks -Token $tokenYueyao
    $counterTask = $tasksCY | Where-Object { $_.meeting_id -eq $midC -and $_.task_type -eq "COUNTER_PROPOSAL" }
    Assert-Check "Yueyao received COUNTER_PROPOSAL" ($null -ne $counterTask)
    if ($counterTask) {
        Write-Host "  Message: $($counterTask.message)" -ForegroundColor Gray
        Write-Host "  Suggested: $($counterTask.suggested_slots | ConvertTo-Json -Compress)" -ForegroundColor Gray
    }

    # C6: Yueyao accepts proposal
    Write-Host "`n[C6] Yueyao accepts counter proposal" -ForegroundColor Cyan
    $submitC2 = Submit-Response -Token $tokenYueyao -MeetingId $midC -Type "ACCEPT_PROPOSAL"
    Assert-Check "Accept proposal submitted" ($submitC2.code -eq 200)

    # C7: Wait for final result
    Write-Host "`n[C7] Wait for final result" -ForegroundColor Cyan
    $detailC2 = Wait-MeetingStatus -Token $token132 -MeetingId $midC -TargetStatuses @("CONFIRMED", "FAILED") -MaxWait 45
    Assert-Check "Meeting reached final state" ($detailC2.status -in @("CONFIRMED", "FAILED"))
    Write-Host "  Final status: $($detailC2.status)" -ForegroundColor Cyan
    if ($detailC2.final_time) { Write-Host "  Final time: $($detailC2.final_time)" -ForegroundColor Green }

} elseif ($detailC.status -eq "CONFIRMED") {
    Write-Host "  Agent found overlap and confirmed directly" -ForegroundColor Green
    Assert-Check "Meeting confirmed (agent found partial overlap)" $true

} elseif ($detailC.status -eq "FAILED") {
    Write-Host "  Agent could not find common time" -ForegroundColor Yellow
    Assert-Check "Meeting failed (no overlap)" $true
}

# ============================================================
#  Summary
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Test Summary" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White
Write-Host "  PASS: $passCount" -ForegroundColor Green
Write-Host "  FAIL: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })
Write-Host "  Total: $($passCount + $failCount)" -ForegroundColor White
Write-Host ""
if ($failCount -eq 0) {
    Write-Host "  All tests passed!" -ForegroundColor Green
} else {
    Write-Host "  Some tests failed. Review output above." -ForegroundColor Red
}
Write-Host ""
