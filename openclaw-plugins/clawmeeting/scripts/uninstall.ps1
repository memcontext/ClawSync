# ClawMeeting Uninstall Script (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ErrorActionPreference = "Continue"
$openclawDir = Join-Path $env:USERPROFILE ".openclaw"

Write-Host ""
Write-Host "===== ClawMeeting Uninstall =====" -ForegroundColor Cyan

# 1. Remove plugin code
$extensionDir = Join-Path $openclawDir "extensions\clawmeeting"
if (Test-Path $extensionDir) {
    Remove-Item -Recurse -Force $extensionDir
    Write-Host "[OK] Removed plugin code: $extensionDir" -ForegroundColor Green
} else {
    Write-Host "[--] Plugin code not found: $extensionDir" -ForegroundColor Gray
}

# 2. Remove runtime data
$dataDir = Join-Path $openclawDir "clawmeeting"
if (Test-Path $dataDir) {
    Remove-Item -Recurse -Force $dataDir
    Write-Host "[OK] Removed runtime data: $dataDir" -ForegroundColor Green
} else {
    Write-Host "[--] Runtime data not found: $dataDir" -ForegroundColor Gray
}

# 3. Clean openclaw.json using node (avoids PowerShell JSON edge cases)
$configPath = Join-Path $openclawDir "openclaw.json"
if (Test-Path $configPath) {
    $nodeScript = @"
const fs = require('fs');
const configPath = process.argv[1];
try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    let changed = false;
    if (config.plugins && Array.isArray(config.plugins.allow)) {
        const before = config.plugins.allow.length;
        config.plugins.allow = config.plugins.allow.filter(p => p !== 'clawmeeting');
        if (config.plugins.allow.length < before) { console.log('[OK] plugins.allow: removed clawmeeting'); changed = true; }
    }
    if (config.plugins && config.plugins.entries && config.plugins.entries.clawmeeting) {
        delete config.plugins.entries.clawmeeting;
        console.log('[OK] plugins.entries: removed clawmeeting'); changed = true;
    }
    if (config.plugins && config.plugins.installs && config.plugins.installs.clawmeeting) {
        delete config.plugins.installs.clawmeeting;
        console.log('[OK] plugins.installs: removed clawmeeting'); changed = true;
    }
    if (config.gateway && config.gateway.tools && Array.isArray(config.gateway.tools.allow)) {
        const before = config.gateway.tools.allow.length;
        config.gateway.tools.allow = config.gateway.tools.allow.filter(t => t !== 'sessions_send' && t !== 'message');
        if (config.gateway.tools.allow.length < before) { console.log('[OK] gateway.tools.allow: removed sessions_send, message'); changed = true; }
    }
    if (changed) {
        fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf-8');
        console.log('[OK] openclaw.json updated');
    } else {
        console.log('[--] No clawmeeting config found in openclaw.json');
    }
} catch (err) {
    console.error('[!!] Failed to clean openclaw.json:', err.message);
}
"@
    node -e $nodeScript $configPath
} else {
    Write-Host "[--] openclaw.json not found: $configPath" -ForegroundColor Gray
}

Write-Host ""
Write-Host "===== Uninstall Complete =====" -ForegroundColor Cyan
Write-Host "To reinstall: openclaw plugin install clawmeeting" -ForegroundColor Yellow
Write-Host "Then restart: openclaw gateway restart" -ForegroundColor Yellow
Write-Host ""
