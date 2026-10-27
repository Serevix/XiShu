# 启动本项目的 API、分析 Worker、记忆 Worker 和 Beat，后台进程窗口保持隐藏。
param([string]$Python = 'python', [int]$Port = 8001, [ValidateRange(1,8)][int]$Workers = 2)
# -Python 指定解释器，-Port 指定网页端口，-Workers 指定分析进程数，其他组件各启动一个进程。
$ErrorActionPreference = 'Stop'
# 根据脚本所在目录找到项目根目录，所有 Python 进程从根目录启动，以便正确导入 backend 包。
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLogRoot = Join-Path $taskRoot 'storage\traces'
New-Item -ItemType Directory -Path $taskLogRoot -Force | Out-Null
$env:PYTHONUTF8 = '1'
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "端口 $Port 已占用，请先停止已有服务或选择其他端口。"
}
# 启动本项目的 Redis 容器。PostgreSQL 使用已有 langgraph-postgres 容器，需要提前确保它在运行。
$taskRedisNames = docker ps -a --filter 'name=^/agent-analytics-redis$' --format '{{.Names}}'
if ($taskRedisNames) {
    docker start agent-analytics-redis | Out-Null
} else {
    # 把本机 6380 映射到容器 6379，并使用 AOF 和数据卷保存 Redis 数据。任务记录仍保存在 PostgreSQL。
    docker run -d --name agent-analytics-redis -p 127.0.0.1:6380:6379 -v agent-analytics-redis-data:/data redis:7-alpine redis-server --appendonly yes | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw 'Redis 启动失败。' }
# 只启动已有的 Milvus 容器，不创建或覆盖项目一的 Milvus 部署。
if (docker ps -a --filter 'name=^/milvus-standalone$' --format '{{.Names}}') {
    docker start milvus-standalone | Out-Null
}
$taskProcesses = @()
# 这里启动职责不同的进程，不是重复启动 API。Beat 定时发送恢复任务，对应 Worker 才执行任务。
$taskLaunches = @(
    @{ Name='api'; Arguments=@('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$Port") },
    @{ Name='beat'; Arguments=@('-m','celery','-A','backend.workers.celery_app:celery_app','beat','--loglevel=info','--schedule','storage/traces/celerybeat-schedule') },
    @{ Name='memory-worker'; Arguments=@('-m','celery','-A','backend.workers.celery_app:celery_app','worker','--pool=solo','--loglevel=info','--queues','analytics-memory','--hostname','analytics-memory@%h') }
)
for ($taskWorkerIndex = 1; $taskWorkerIndex -le $Workers; $taskWorkerIndex++) {
    # Windows 下使用多个独立 solo 进程处理分析，避免共用事件循环和 Matplotlib 状态。
    $taskLaunches += @{ Name="worker-$taskWorkerIndex"; Arguments=@('-m','celery','-A','backend.workers.celery_app:celery_app','worker','--pool=solo','--loglevel=info','--queues','celery','--hostname',"analytics-$taskWorkerIndex@%h") }
}
foreach ($taskLaunch in $taskLaunches) {
    # 隐藏进程窗口，并把输出和错误分别写入日志。PassThru 返回进程对象，用来记录进程 ID。
    $taskProcess = Start-Process -FilePath $Python -ArgumentList $taskLaunch.Arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogRoot "$($taskLaunch.Name).out.log") -RedirectStandardError (Join-Path $taskLogRoot "$($taskLaunch.Name).err.log")
    $taskProcesses += @{ Name=$taskLaunch.Name; Id=$taskProcess.Id }
}
# 保存本次启动的进程 ID，停止脚本只检查这些进程，不按名称停止所有 Python 进程。
$taskProcesses | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogRoot 'processes.json') -Encoding utf8
$taskReady = $false
$taskReadyDeadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $taskReadyDeadline) {
    # 轮询应用、数据库和 Redis 的健康检查，确认 API 可用。此检查不等于所有 Worker 已完成验证。
    try {
        $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($taskHealth.application -and $taskHealth.database -and $taskHealth.redis) { $taskReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $taskReady) { throw "API 在启动等待内未就绪，请检查日志；进程记录已保存，可运行 stop.ps1。" }
Write-Output "工作台：http://127.0.0.1:$Port；Worker 数：$Workers；日志目录：$taskLogRoot"
