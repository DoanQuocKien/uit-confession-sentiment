# Silent, self-healing full classification run (no popups or beeps; everything goes to supervisor.log).
# Restarts Ollama if it is down, restarts classify.py if it crashes or stalls (it resumes from
# llm_cache.jsonl), and re-runs when some posts came back as "error" (errors are retried).
# Usage: powershell -File run_full.ps1
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root, wherever it lives
$py = (Resolve-Path ".venv\Scripts\python.exe").Path; $wd = (Get-Location).Path
# OLLAMA_MODELS (where the model store lives) is inherited from your environment; set OLLAMA_EXE if ollama is not on PATH
$env:OLLAMA_HOST = "127.0.0.1:11434"; $env:PYTHONIOENCODING = "utf-8"
$ollama = if ($env:OLLAMA_EXE) { $env:OLLAMA_EXE } else { "ollama" }
$log = "full_run.log"; $cache = "llm_cache.jsonl"; $maxAttempts = 40; $stallMin = 8

function Log($m) { "$(Get-Date -Format s) $m" | Add-Content supervisor.log }
function ServerUp { try { [void](Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:11434/api/version); $true } catch { $false } }
function EnsureServer {
    foreach ($try in 1..3) {
        if (ServerUp) { return $true }
        Log "ollama not responding: restarting it (try $try)"
        Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep 3
        Start-Process -FilePath $ollama -ArgumentList serve -WindowStyle Hidden -RedirectStandardError ollama_serve.log
        foreach ($i in 1..20) { Start-Sleep 3; if (ServerUp) { return $true } }
    }
    $false
}
function Count($f) { if (Test-Path $f) { (Get-Content $f | Measure-Object -Line).Lines } else { 0 } }

$start = Get-Date; $errRuns = 0
Log "supervisor started"
for ($a = 1; $a -le $maxAttempts; $a++) {
    if (-not (EnsureServer)) { Log "attempt ${a}: ollama will not start, waiting 2 min"; Start-Sleep 120; continue }
    $p = Start-Process -FilePath $py -WorkingDirectory $wd -ArgumentList "-u", "classify.py" `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
    $last = Count $cache; $lastMove = Get-Date
    Log "attempt $a started, $last labels cached"
    while (-not $p.HasExited) {
        Start-Sleep 30
        $c = Count $cache
        if ($c -gt $last) { $last = $c; $lastMove = Get-Date }
        elseif (((Get-Date) - $lastMove).TotalMinutes -ge $stallMin -and $c -lt 2914) {
            # stall while labeling (cache not growing): restart the classifier, and Ollama if it is wedged
            Log "stall at $c labels for $stallMin min: restarting classify.py"
            Stop-Process -Id $p.Id -Force; if (-not (ServerUp)) { Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force }
            break
        }
        if ($c -ge 2914 -and ((Get-Date) - $lastMove).TotalMinutes -ge 25) { Log "post-labeling step (PhoBERT/report) slow for 25 min: restarting"; Stop-Process -Id $p.Id -Force; break }
    }
    $p.WaitForExit(15000) | Out-Null
    $fresh = (Test-Path results.csv) -and ((Get-Item results.csv).LastWriteTime -gt $start)
    if ($p.HasExited -and $p.ExitCode -eq 0 -and $fresh) {
        $rows = Import-Csv results.csv; $err = @($rows | Where-Object { $_.llm_label -eq "error" }).Count
        Log "classify finished: $($rows.Count) rows, $err error labels"
        if ($err -eq 0 -or $errRuns -ge 3) { Log "DONE ($err errors left)"; exit 0 }
        $errRuns++; Log "re-running to retry $err errored posts (retry round $errRuns)"; continue
    }
    Log "attempt $a ended (exit $($p.ExitCode)) at $last labels; retrying in 30s"
    Start-Sleep 30
}
Log "GAVE UP after $maxAttempts attempts"
exit 1
