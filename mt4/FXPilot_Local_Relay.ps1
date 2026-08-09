$ErrorActionPreference = 'Stop'
$filesDir = Join-Path $env:APPDATA 'MetaQuotes\Terminal\Common\Files'
$jobPath = Join-Path $filesDir 'fxpilot_job.json'
$workPath = Join-Path $filesDir 'fxpilot_job.sending.json'
$statusPath = Join-Path $filesDir 'fxpilot_relay_status.txt'
New-Item -ItemType Directory -Force -Path $filesDir | Out-Null

Write-Host 'FXPilot Local Relay is running. Keep this window open.' -ForegroundColor Cyan
Write-Host "Watching: $jobPath"

while ($true) {
    try {
        if (Test-Path $jobPath) {
            Move-Item -Force $jobPath $workPath
            $job = Get-Content -Raw -Encoding UTF8 $workPath | ConvertFrom-Json
            $body = $job.payload | ConvertTo-Json -Depth 20 -Compress
            $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $job.url `
                -Headers @{ 'X-FXPilot-MT4-Token' = [string]$job.token } `
                -ContentType 'application/json' -Body $body -TimeoutSec 90
            $line = "OK $($response.StatusCode) $(Get-Date -Format s)"
            Set-Content -Encoding UTF8 -Path $statusPath -Value $line
            Write-Host $line -ForegroundColor Green
            Remove-Item -Force $workPath
        }
    } catch {
        $line = "ERROR $(Get-Date -Format s) $($_.Exception.Message)"
        Set-Content -Encoding UTF8 -Path $statusPath -Value $line
        Write-Host $line -ForegroundColor Red
        if (Test-Path $workPath) { Move-Item -Force $workPath $jobPath }
        Start-Sleep -Seconds 5
    }
    Start-Sleep -Milliseconds 500
}
