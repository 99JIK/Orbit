<#
.SYNOPSIS
    Orbit 수집 API를 Windows 작업 스케줄러에 등록한다. 설치 절차는 docs/deploy.md 5장.

.DESCRIPTION
    부팅 시 사용자 로그온 여부와 관계없이 시작하고, 비정상 종료되면 1분 뒤 다시 시작한다.
    관리자 권한 PowerShell에서 실행한다. 실행 중 현재 사용자 암호를 묻는다.

.EXAMPLE
    .\install-task.ps1 -ServerExe C:\Github\Orbit\server\.venv\Scripts\orbit-server.exe
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$ServerExe,
    [string]$OrbitHome = (Join-Path $env:USERPROFILE 'orbit'),
    [int]$Port = 8787,
    [string]$TaskName = 'Orbit Ingest'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ServerExe)) {
    throw "orbit-server 실행 파일을 찾을 수 없습니다: $ServerExe"
}

# 작업 스케줄러는 작업별 환경 변수를 지원하지 않으므로 cmd 래퍼로 ORBIT_HOME을 넘긴다.
$command = "set ORBIT_HOME=$OrbitHome&& `"$ServerExe`" serve --port $Port"
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $command"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

$credential = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message 'Orbit 작업을 실행할 계정 암호'
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "등록 완료: '$TaskName' (ORBIT_HOME=$OrbitHome, port=$Port)"
