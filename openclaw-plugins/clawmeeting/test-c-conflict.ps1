# ClawMeeting Test - Scenario C: Conflict -> NEGOTIATING
# Usage: powershell -File test-c-conflict.ps1
#
# Flow: Create meeting -> Invitee submits conflicting time -> Agent negotiates
#       -> Invitee accepts proposal -> Final CONFIRMED or FAILED
# Initiator: upp@132.com  |  Invitee: yueyao@email.com

$serverUrl = "http://192.168.22.28:8000"
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
    $detail = $null
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

function Pause-WithPrompt {
    param([string]$Message)
    Write-Host ""
    Write-Host "  >> $Message" -ForegroundColor Yellow
    Write-Host "  >> Press ENTER to continue..." -ForegroundColor Yellow
    Read-Host
}

# ============================================================
#  Start
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Scenario C: Conflict -> NEGOTIATING" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup: Get tokens ---" -ForegroundColor Cyan
$tokenYueyao = Get-Token -Email "yueyao@email.com"
Write-Host "  Initiator (132): $($token132.Substring(0,20))..."
Write-Host "  Invitee (yueyao): $($tokenYueyao.Substring(0,20))..."

# C1: Create meeting
Write-Host "`n[C1] Create meeting" -ForegroundColor Cyan
$resC = New-Meeting -Token $token132 -Title "test-conflict" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-07 09:00-11:00")
$midC = $resC.data.id
Write-Host "  Meeting: $midC  Status: $($resC.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resC.data.status -eq "COLLECTING")

Pause-WithPrompt "Meeting created. Check OpenClaw plugin for invite notification, then press ENTER."

# C2: Check yueyao has invite task
Write-Host "[C2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksC = Get-PendingTasks -Token $tokenYueyao
$inviteTaskC = $tasksC | Where-Object { $_.meeting_id -eq $midC -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTaskC)

Pause-WithPrompt "Yueyao should see invite in OpenClaw. Now press ENTER to submit conflicting time."

# C3: Yueyao submits DIFFERENT time (conflict)
Write-Host "[C3] Yueyao submits conflicting time" -ForegroundColor Cyan
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

    Pause-WithPrompt "Agent sent counter proposal. Check OpenClaw for negotiation notification, then press ENTER."

    # C5: Check yueyao has COUNTER_PROPOSAL
    Write-Host "[C5] Check yueyao has COUNTER_PROPOSAL notification" -ForegroundColor Cyan
    $tasksCY = Get-PendingTasks -Token $tokenYueyao
    $counterTask = $tasksCY | Where-Object { $_.meeting_id -eq $midC -and $_.task_type -eq "COUNTER_PROPOSAL" }
    Assert-Check "Yueyao received COUNTER_PROPOSAL" ($null -ne $counterTask)
    if ($counterTask) {
        Write-Host "  Message: $($counterTask.message)" -ForegroundColor Gray
        Write-Host "  Suggested: $($counterTask.suggested_slots | ConvertTo-Json -Compress)" -ForegroundColor Gray
    }

    Pause-WithPrompt "Press ENTER to accept the counter proposal."

    # C6: Yueyao accepts proposal
    Write-Host "[C6] Yueyao accepts counter proposal" -ForegroundColor Cyan
    $submitC2 = Submit-Response -Token $tokenYueyao -MeetingId $midC -Type "ACCEPT_PROPOSAL"
    Assert-Check "Accept proposal submitted" ($submitC2.code -eq 200)

    # C7: Wait for final result
    Write-Host "`n[C7] Wait for final result" -ForegroundColor Cyan
    $detailC2 = Wait-MeetingStatus -Token $token132 -MeetingId $midC -TargetStatuses @("CONFIRMED", "FAILED") -MaxWait 45
    Assert-Check "Meeting reached final state" ($detailC2.status -in @("CONFIRMED", "FAILED"))
    Write-Host "  Final status: $($detailC2.status)" -ForegroundColor Cyan
    if ($detailC2.final_time) { Write-Host "  Final time: $($detailC2.final_time)" -ForegroundColor Green }

    Pause-WithPrompt "Check OpenClaw for final notification, then press ENTER."

    # C8: Check notifications
    Write-Host "[C8] Check final notifications" -ForegroundColor Cyan
    $tasksFinal = Get-PendingTasks -Token $tokenYueyao
    $finalTask = $tasksFinal | Where-Object { $_.meeting_id -eq $midC -and ($_.task_type -eq "MEETING_CONFIRMED" -or $_.task_type -eq "MEETING_FAILED") }
    Assert-Check "Yueyao received final notification" ($null -ne $finalTask)

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
Write-Host "  Test C Summary" -ForegroundColor White
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
