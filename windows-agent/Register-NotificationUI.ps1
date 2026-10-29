param(
  [Parameter(Mandatory=$true)][string]$UIUser,
  [Parameter(Mandatory=$true)][string]$InstallDirectory
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$account = New-Object Security.Principal.NTAccount $UIUser
$sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
$uiScript = Join-Path $InstallDirectory 'HospitalRemoteUI.ps1'
if (-not (Test-Path -LiteralPath $uiScript -PathType Leaf)) { throw "Notification UI script not found: $uiScript" }
$powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$taskFolder = $taskService.GetFolder('\')
$task = $taskService.NewTask(0)
$task.RegistrationInfo.Description = 'Shows local remote-session notices and the offline emergency stop.'
$task.Principal.UserId = $UIUser
$task.Principal.LogonType = 3
$task.Principal.RunLevel = 0
$trigger = $task.Triggers.Create(9)
$trigger.UserId = $UIUser
$trigger.Enabled = $true
$action = $task.Actions.Create(0)
$action.Path = $powerShell
$action.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy AllSigned -File `"$uiScript`""
$action.WorkingDirectory = $InstallDirectory
$task.Settings.RestartCount = 5
$task.Settings.RestartInterval = 'PT1M'
$task.Settings.ExecutionTimeLimit = 'PT0S'
$taskFolder.RegisterTaskDefinition('Hospital Remote Notification UI', $task, 6, $UIUser, $null, 3, $null) | Out-Null
if ($identity.User.Value -eq $sid) { $taskFolder.GetTask('Hospital Remote Notification UI').Run($null) | Out-Null }
