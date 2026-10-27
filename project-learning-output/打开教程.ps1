# 仅启动本目录的静态阅读服务，不启动业务项目、不访问数据库。
param([ValidateRange(1024,65535)][int]$Port = 8420)
$ErrorActionPreference = 'Stop'
Write-Output "Open http://127.0.0.1:$Port ; press Ctrl+C to stop the tutorial server."
python -m http.server $Port --bind 127.0.0.1 --directory $PSScriptRoot
