# Blast Radius: bring the demo up on this machine.
#   .\scripts\lab.ps1 up       check Docker + cluster, start the console, run the doctor, open the screens
#   .\scripts\lab.ps1 doctor   health check only (add -Live to test the Claude login)
#   .\scripts\lab.ps1 reset    clear every fault
param([ValidateSet("up", "doctor", "reset")][string]$Action = "up", [switch]$Live)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:KUBECONFIG = "$env:USERPROFILE\.kube\blast-radius.kubeconfig"
$wg = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
$env:PATH = "$env:PATH;$wg\Kubernetes.kind_Microsoft.Winget.Source_8wekyb3d8bbwe;$wg\Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe\windows-amd64;$wg\Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

if ($Action -eq "reset") { uv run python -m noc.faults reset; exit $LASTEXITCODE }
if ($Action -eq "doctor") { if ($Live) { uv run python -m noc.doctor --live } else { uv run python -m noc.doctor }; exit $LASTEXITCODE }

Step "Docker"
docker info --format "{{.ServerVersion}}" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it, wait for 'Engine running', then re-run." }

Step "Cluster"
$state = docker inspect -f "{{.State.Status}}" blast-radius-control-plane 2>$null
if ($state -ne "running") {
    throw "The kind node is '$state'. etcd lives in tmpfs, so a stopped node has lost the cluster: rebuild with terraform (see docs/runbook.md, 'Rebuild')."
}
kubectl get nodes --no-headers

Step "Console"
$up = $false
try { $up = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/health -TimeoutSec 3).StatusCode -eq 200 } catch {}
if (-not $up) {
    Start-Process -WindowStyle Minimized -FilePath "uv" -ArgumentList "run", "python", "-m", "noc.console" -WorkingDirectory $Root
    for ($i = 0; $i -lt 30 -and -not $up; $i++) {
        Start-Sleep -Seconds 1
        try { $up = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/health -TimeoutSec 3).StatusCode -eq 200 } catch {}
    }
    if (-not $up) { throw "The console did not start: run 'uv run python -m noc.console' to see why." }
}
Write-Host "console up at http://127.0.0.1:8765"

Step "Doctor"
uv run python -m noc.doctor
$ok = $LASTEXITCODE -eq 0

Step "Screens"
Start-Process "http://127.0.0.1:8765/"
Start-Process "http://localhost:3000/"
if (-not $ok) { Write-Host "`nThe doctor found failures above. Fix them, or present from Replay mode." -ForegroundColor Yellow; exit 1 }
Write-Host "`nReady." -ForegroundColor Green
