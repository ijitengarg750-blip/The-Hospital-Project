param([switch]$RemoveConfiguration)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal $identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this uninstaller from an elevated PowerShell window.' }

$program = Join-Path $env:ProgramFiles 'HospitalRemoteAgent'
$data = Join-Path $env:ProgramData 'HospitalRemoteAgent'
$launcherRecord = Join-Path $data 'ui-launcher-path.txt'
if (Test-Path -LiteralPath $launcherRecord) {
  $launcher = [IO.File]::ReadAllText($launcherRecord, [Text.Encoding]::Unicode)
  if ([IO.Path]::GetFileName($launcher) -ne 'HospitalRemoteUI.cmd' -or (Split-Path -Parent $launcher) -notlike '*\Microsoft\Windows\Start Menu\Programs\Startup') { throw 'Refusing to remove an unexpected UI launcher path.' }
  if (Test-Path -LiteralPath $launcher) { Remove-Item -LiteralPath $launcher -Force }
}
$agent = Join-Path $program 'hospital-remote-agent.exe'
if (Test-Path -LiteralPath $agent) { & $agent uninstall }
if (Test-Path -LiteralPath $program) { Remove-Item -LiteralPath $program -Recurse -Force }
if ($RemoveConfiguration) {
  if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force }
}
Write-Host 'Hospital Remote Agent and per-user notification launcher removed.'
