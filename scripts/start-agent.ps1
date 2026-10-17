param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run uv sync --all-groups and prepare the ML environment first.' }
if (-not (Test-Path -LiteralPath 'storage/models/agent-tokenizer/agent_manifest.json')) {
    throw 'Prepare the matching tokenizer first. See README Paper Agent setup.'
}
$env:PAPER_RAG_AGENT_RUNTIME_ENABLED = 'true'
$env:PAPER_RAG_AGENT_ACTION_PROTOCOL = 'json'
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = '127.0.0.1,localhost'
$env:LANGCHAIN_TRACING_V2 = 'false'
$env:LANGSMITH_TRACING = 'false'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, then retry.' }
$migrationCode = "import os; from app.core.config import get_settings; from alembic.config import Config; from alembic import command; os.environ['PAPER_RAG_DATABASE_URL']=get_settings().database_url; command.upgrade(Config('alembic.ini'),'head')"
& $pythonPath -c $migrationCode
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
$logRoot = Join-Path $projectRoot 'storage/logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
if (-not (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)) {
    $api = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'agent-api.out.log') -RedirectStandardError (Join-Path $logRoot 'agent-api.err.log')
    Write-Output "API PID: $($api.Id)"
} else { Write-Output 'Port 8000 already in use; restart that API with PAPER_RAG_AGENT_RUNTIME_ENABLED=true if Agent is disabled.' }
$existingWorkers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m arq app.workers.settings.WorkerSettings' })
if ($existingWorkers.Count -eq 0) {
    $worker = Start-Process -FilePath $pythonPath -ArgumentList '-m','arq','app.workers.settings.WorkerSettings' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'agent-worker.out.log') -RedirectStandardError (Join-Path $logRoot 'agent-worker.err.log')
    Write-Output "Worker PID: $($worker.Id)"
} else { Write-Output 'Worker already running; restart it after code/config changes.' }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue)) {
    $nodePath = (Get-Command node).Source
    $vitePath = Join-Path $projectRoot 'frontend/node_modules/vite/bin/vite.js'
    if (-not (Test-Path -LiteralPath $vitePath)) { throw 'Run npm --prefix frontend ci first.' }
    $web = Start-Process -FilePath $nodePath -ArgumentList $vitePath,'--host','127.0.0.1','--port','5173' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'agent-web.out.log') -RedirectStandardError (Join-Path $logRoot 'agent-web.err.log')
    Write-Output "Frontend PID: $($web.Id)"
}
Write-Output 'Open http://127.0.0.1:5173/agent. Logs: storage/logs/agent-*'
