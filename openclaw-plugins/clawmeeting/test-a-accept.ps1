# ClawMeeting Test - Scenario A: Accept -> CONFIRMED
# Usage: powershell -File test-a-accept.ps1
#
# Flow: Create meeting -> Invitee accepts -> Both receive CONFIRMED notification
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
Write-Host "  Scenario A: Accept -> CONFIRMED" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup: Get tokens ---" -ForegroundColor Cyan
$tokenYueyao = Get-Token -Email "yueyao@email.com"
Write-Host "  Initiator (132): $($token132.Substring(0,20))..."
Write-Host "  Invitee (yueyao): $($tokenYueyao.Substring(0,20))..."

# A1: Create meeting
Write-Host "`n[A1] Create meeting" -ForegroundColor Cyan
$resA = New-Meeting -Token $token132 -Title "test-accept" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-05 10:00-12:00")
$midA = $resA.data.id
Write-Host "  Meeting: $midA  Status: $($resA.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resA.data.status -eq "COLLECTING")

Pause-WithPrompt "Meeting created. Check OpenClaw plugin for invite notification, then press ENTER."

# A2: Check yueyao has invite task
Write-Host "[A2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksA = Get-PendingTasks -Token $tokenYueyao
$inviteTask = $tasksA | Where-Object { $_.meeting_id -eq $midA -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTask)

Pause-WithPrompt "Yueyao should see invite in OpenClaw. Now press ENTER to submit accept."

# A3: Yueyao submits same time (accept)
Write-Host "[A3] Yueyao submits availability (accept)" -ForegroundColor Cyan
$submitA = Submit-Response -Token $tokenYueyao -MeetingId $midA -Type "INITIAL" -Slots @("2026-04-05 10:00-12:00")
Write-Host "  all_submitted: $($submitA.data.all_submitted)"
Assert-Check "All submitted after yueyao responds" ($submitA.data.all_submitted -eq $true)

# A4: Wait for Agent -> CONFIRMED
Write-Host "`n[A4] Wait for Agent processing" -ForegroundColor Cyan
$detailA = Wait-MeetingStatus -Token $token132 -MeetingId $midA -TargetStatuses @("CONFIRMED")
Assert-Check "Meeting status is CONFIRMED" ($detailA.status -eq "CONFIRMED")
if ($detailA.final_time) { Write-Host "  Final time: $($detailA.final_time)" -ForegroundColor Green }

Pause-WithPrompt "Meeting CONFIRMED. Check OpenClaw for confirmation notification, then press ENTER."

# A5: Check initiator has CONFIRMED notification
Write-Host "[A5] Check initiator (132) notification" -ForegroundColor Cyan
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
#  Summary
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Test A Summary" -ForegroundColor White
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
