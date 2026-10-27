# 读取启动脚本记录的进程 ID，确认仍是项目模块后停止这些进程。
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskManifest = Join-Path $taskRoot 'storage\traces\processes.json'
if (-not (Test-Path -LiteralPath $taskManifest)) { Write-Output '没有项目进程记录。'; exit }
$taskProcesses = Get-Content -LiteralPath $taskManifest -Raw | ConvertFrom-Json
foreach ($taskRecorded in $taskProcesses) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskRecorded.Id)" -ErrorAction SilentlyContinue
    # 旧进程 ID 可能已分配给其他进程，因此停止前还要检查其命令行是否匹配项目模块。
    # 只停止记录的应用进程，不停止 PostgreSQL、Redis 或 Milvus 容器。
    if ($taskProcess -and $taskProcess.CommandLine -match 'backend\.main:app|backend\.workers\.celery_app:celery_app') {
        Stop-Process -Id $taskRecorded.Id
    }
}
Write-Output '已停止本项目 API、Worker 和 Beat；数据库及 Redis 保持运行。'
