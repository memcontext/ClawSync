# ClawMeeting Test - Scenario B: Reject -> FAILED
# Usage: powershell -File test-b-reject.ps1
#
# Flow: Create meeting -> Invitee rejects -> Initiator receives FAILED notification
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
Write-Host "  Scenario B: Reject -> FAILED" -ForegroundColor White
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup: Get tokens ---" -ForegroundColor Cyan
$tokenYueyao = Get-Token -Email "yueyao@email.com"
Write-Host "  Initiator (132): $($token132.Substring(0,20))..."
Write-Host "  Invitee (yueyao): $($tokenYueyao.Substring(0,20))..."

# B1: Create meeting
Write-Host "`n[B1] Create meeting" -ForegroundColor Cyan
$resB = New-Meeting -Token $token132 -Title "test-reject" -Duration 30 -Invitees @("yueyao@email.com") -Slots @("2026-04-06 14:00-16:00")
$midB = $resB.data.id
Write-Host "  Meeting: $midB  Status: $($resB.data.status)"
Assert-Check "Meeting created with COLLECTING status" ($resB.data.status -eq "COLLECTING")

Pause-WithPrompt "Meeting created. Check OpenClaw plugin for invite notification, then press ENTER."

# B2: Check yueyao has invite task
Write-Host "[B2] Check yueyao pending tasks (invite notification)" -ForegroundColor Cyan
$tasksB = Get-PendingTasks -Token $tokenYueyao
$inviteTaskB = $tasksB | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "INITIAL_SUBMIT" }
Assert-Check "Yueyao received INITIAL_SUBMIT task" ($null -ne $inviteTaskB)

Pause-WithPrompt "Yueyao should see invite in OpenClaw. Now press ENTER to submit reject."

# B3: Yueyao rejects
Write-Host "[B3] Yueyao rejects meeting" -ForegroundColor Cyan
$submitB = Submit-Response -Token $tokenYueyao -MeetingId $midB -Type "REJECT" -Note "I'm not available"
Write-Host "  all_submitted: $($submitB.data.all_submitted)"
Assert-Check "Rejection recorded" ($submitB.code -eq 200)

# B4: Wait for Agent -> FAILED
Write-Host "`n[B4] Wait for Agent processing" -ForegroundColor Cyan
$detailB = Wait-MeetingStatus -Token $token132 -MeetingId $midB -TargetStatuses @("FAILED")
Assert-Check "Meeting status is FAILED" ($detailB.status -eq "FAILED")

Pause-WithPrompt "Meeting FAILED. Check OpenClaw for initiator's failure notification, then press ENTER."

# B5: Initiator gets FAILED notification
Write-Host "[B5] Check initiator (132) notification" -ForegroundColor Cyan
$tasks132B = Get-PendingTasks -Token $token132
$failTask = $tasks132B | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "MEETING_FAILED" }
Assert-Check "Initiator received MEETING_FAILED" ($null -ne $failTask)

# B6: Yueyao should NOT get FAILED notification
Write-Host "`n[B6] Check yueyao has no FAILED notification" -ForegroundColor Cyan
$tasksYB = Get-PendingTasks -Token $tokenYueyao
$failTaskY = $tasksYB | Where-Object { $_.meeting_id -eq $midB -and $_.task_type -eq "MEETING_FAILED" }
Assert-Check "Yueyao has NO MEETING_FAILED task" ($null -eq $failTaskY)

# ============================================================
#  Summary
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  Test B Summary" -ForegroundColor White
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
