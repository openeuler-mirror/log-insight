# log-insight: 每日 23:00 由「计划任务」调用, 将本地 dev 分支推送到远端
# 说明: 仅推送已有提交, 不会自动提交工作区改动; 日志写入 %LOCALAPPDATA%\loginsight\push-dev.log

$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA 'loginsight'
$log = Join-Path $logDir 'push-dev.log'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $repo

"[{0}] ==== push dev start ====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') |
    Out-File -Append -Encoding utf8 $log

$output = & git push origin dev 2>&1 | Out-String
$output.Trim() | Out-File -Append -Encoding utf8 $log

"[{0}] ==== push dev done (exit {1}) ====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE |
    Out-File -Append -Encoding utf8 $log