# ClawMeeting - Quick CONFIRMED Test
# Usage: powershell -File test-confirm-only.ps1
#
# 目的: 发起一个会议，等待插件自动提交，等待 CONFIRMED，检查 Telegram 是否收到推送

$serverUrl = "http://39.105.143.2:7010"
$tokenInitiator = "sk-UUw_9i3qeZSA5x7wbf22b6cc6bc27f56"
$inviteeEmail = "uppxxcco@gmail.com"

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

function Get-MeetingDetail {
    param([string]$Token, [string]$MeetingId)
    $headers = @{ "Authorization" = "Bearer $Token" }
    $res = Invoke-RestMethod -Uri "$serverUrl/api/meetings/$MeetingId" -Method GET -Headers $headers
    return $res.data
}

function Wait-MeetingStatus {
    param([string]$Token, [string]$MeetingId, [string[]]$TargetStatuses, [int]$MaxWait = 180)
    Write-Host "  Waiting for status (max ${MaxWait}s)..." -ForegroundColor Yellow
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
    Write-Host "  -> Timeout! Current: $($detail.status)" -ForegroundColor Red
    return $detail
}

# ============================================================
#  Main
# ============================================================

Write-Host "`n============================================" -ForegroundColor White
Write-Host "  ClawMeeting CONFIRMED Push Test" -ForegroundColor White
Write-Host "  Invitee: $inviteeEmail" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor White

Write-Host "`n--- Setup ---" -ForegroundColor Cyan
$tokenInvitee = Get-Token -Email $inviteeEmail
Write-Host "  Initiator token: $($tokenInitiator.Substring(0,20))..."
Write-Host "  Invitee token:   $($tokenInvitee.Substring(0,20))..."

$tomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
$slot = "$tomorrow 10:00-12:00"
$ts = Get-Date -Format "HHmm"
$title = "TG-Confirm-$ts"

Write-Host "`n--- Create Meeting ---" -ForegroundColor Cyan
Write-Host "  Title: $title"
Write-Host "  Slot:  $slot"
$res = New-Meeting -Token $tokenInitiator -Title $title -Duration 30 -Invitees @($inviteeEmail) -Slots @($slot)
$mid = $res.data.id
Write-Host "  Meeting: $mid  Status: $($res.data.status)"

if ($res.data.status -ne "COLLECTING") {
    Write-Host "  [ERROR] Expected COLLECTING, got $($res.data.status)" -ForegroundColor Red
    exit 1
}

Write-Host "`n--- Wait for CONFIRMED (plugin auto-submit + agent) ---" -ForegroundColor Cyan
$detail = Wait-MeetingStatus -Token $tokenInitiator -MeetingId $mid -TargetStatuses @("CONFIRMED", "FAILED") -MaxWait 180

Write-Host ""
if ($detail.status -eq "CONFIRMED") {
    Write-Host "  [CONFIRMED] $($detail.final_time)" -ForegroundColor Green
    Write-Host "  Meeting link: $($detail.meeting_link)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Check Telegram for push notification containing:" -ForegroundColor Yellow
    Write-Host "    - Title: $title" -ForegroundColor Yellow
    Write-Host "    - Time:  $($detail.final_time)" -ForegroundColor Yellow
    Write-Host "    - Link:  $($detail.meeting_link)" -ForegroundColor Yellow
} elseif ($detail.status -eq "FAILED") {
    Write-Host "  [FAILED] Agent had no available time slots" -ForegroundColor Yellow
    Write-Host "  Check Telegram for FAILED notification" -ForegroundColor Yellow
} else {
    Write-Host "  [TIMEOUT] Status: $($detail.status)" -ForegroundColor Red
}

Write-Host ""
