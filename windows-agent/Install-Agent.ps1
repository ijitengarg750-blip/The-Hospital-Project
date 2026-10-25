param(
  [Parameter(Mandatory=$true)][string]$Server,
  [Parameter(Mandatory=$true)][string]$MachineCode,
  [Parameter(Mandatory=$true)][string]$UIUser,
  [switch]$Legacy
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal $identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer from an elevated PowerShell window.' }

$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$destination = Join-Path $env:ProgramFiles 'HospitalRemoteAgent'
$osVersion = [Version](Get-WmiObject Win32_OperatingSystem).Version
$isLegacyOS = $osVersion.Major -lt 10
if ($isLegacyOS -and -not $Legacy) { throw 'Windows 7/8/8.1 requires -Legacy and a Go 1.20-built legacy artifact.' }
$architecture = if ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64' -or $env:PROCESSOR_ARCHITECTURE -eq 'AMD64') { 'amd64' } else { '386' }
$flavor = if ($Legacy) { "legacy-$architecture" } else { 'modern-amd64' }
$agentBinary = Join-Path $source "build\$flavor\hospital-remote-agent.exe"
if (-not (Test-Path -LiteralPath $agentBinary)) { throw "Missing agent artifact: $agentBinary. Run Build-Agent.ps1 first." }
$account = New-Object Security.Principal.NTAccount $UIUser
$sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
$profile = Get-WmiObject Win32_UserProfile | Where-Object { $_.SID -eq $sid } | Select-Object -First 1
if (-not $profile -or -not $profile.LocalPath) { throw 'The UI user must sign in once before installation so its Windows profile exists.' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item $agentBinary (Join-Path $destination 'hospital-remote-agent.exe') -Force
Copy-Item (Join-Path $source 'ui\HospitalRemoteUI.ps1') $destination -Force

Write-Host 'Paste the one-time agent key when prompted. Input is hidden.'
& (Join-Path $destination 'hospital-remote-agent.exe') install --server $Server --machine $MachineCode --ui-user $UIUser
if ($LASTEXITCODE -ne 0) { throw 'Agent service installation failed.' }

$startup = Join-Path $profile.LocalPath 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
New-Item -ItemType Directory -Path $startup -Force | Out-Null
$launcher = Join-Path $startup 'HospitalRemoteUI.cmd'
$command = "@echo off`r`nstart `"`" powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File `"$destination\HospitalRemoteUI.ps1`"`r`n"
[IO.File]::WriteAllText($launcher, $command, [Text.Encoding]::ASCII)
$launcherRecord = Join-Path (Join-Path $env:ProgramData 'HospitalRemoteAgent') 'ui-launcher-path.txt'
[IO.File]::WriteAllText($launcherRecord, $launcher, [Text.Encoding]::Unicode)
icacls $launcherRecord /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null
Write-Host 'Hospital Remote Agent installed. Sign in as the UI user once to start the notification application.'
