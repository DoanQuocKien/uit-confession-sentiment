# Silent supervisor for crawl_search_v2.py. Waits for the scroll crawl (run_recrawl.ps1) to finish first,
# then searches ids 2016 -> 1 (skipping gaps and known-missing ids), restarting on crashes or stalls.
# Everything is logged to search_supervisor.log.
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root, wherever it lives
$py = (Resolve-Path ".venv\Scripts\python.exe").Path; $wd = (Get-Location).Path
$chrome = if ($env:CHROME_EXE) { $env:CHROME_EXE } else { "C:\Program Files\Google\Chrome\Application\chrome.exe" }
$profileDir = if ($env:CHROME_PROFILE) { $env:CHROME_PROFILE } else { "C:\chrome_fb" }   # separate profile, log in to Facebook once
$env:PYTHONIOENCODING = "utf-8"
$log = "crawl_search_v2.log"; $done = "search_v2_done.txt"; $stallMin = 12; $maxAttempts = 80
function Log($m) { "$(Get-Date -Format s) $m" | Add-Content search_supervisor.log }
function ChromeUp { try { [void](Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 http://localhost:9222/json/version); $true } catch { $false } }
function EnsureChrome {
    if (ChromeUp) { return $true }
    Log "debug Chrome not reachable: starting it"
    Start-Process $chrome -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$profileDir", "--no-first-run", "https://www.facebook.com/UITconfess"
    foreach ($i in 1..20) { Start-Sleep 3; if (ChromeUp) { return $true } }
    $false
}
function Count { if (Test-Path $done) { (Get-Content $done | Measure-Object -Line).Lines } else { 0 } }

Log "starting the search (scroll crawl was stopped by hand; its data is in posts_v2.jsonl)"
for ($a = 1; $a -le $maxAttempts; $a++) {
    if (-not (EnsureChrome)) { Log "attempt ${a}: Chrome will not start, waiting 2 min"; Start-Sleep 120; continue }
    $p = Start-Process -FilePath $py -WorkingDirectory $wd -ArgumentList "-u", "crawl_search_v2.py" `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
    $h = $p.Handle
    $last = Count; $lastMove = Get-Date
    Log "attempt $a started, $last ids searched so far"
    while (-not $p.HasExited) {
        Start-Sleep 30
        $c = Count
        if ($c -gt $last) { $last = $c; $lastMove = Get-Date }
        elseif (((Get-Date) - $lastMove).TotalMinutes -ge $stallMin) {
            Log "no progress for $stallMin min at $c ids: restarting"; Stop-Process -Id $p.Id -Force; break
        }
    }
    $p.WaitForExit(15000) | Out-Null
    $tail = if (Test-Path $log) { (Get-Content $log -Tail 1) } else { "" }
    if ($p.HasExited -and $p.ExitCode -eq 0 -and $tail -match "^done:") { Log "DONE: $tail"; exit 0 }
    Log "attempt $a ended (exit $($p.ExitCode)) at $(Count) ids; $tail"
    Start-Sleep 30
}
Log "GAVE UP after $maxAttempts attempts"
exit 1
