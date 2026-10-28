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

function Assert-ValidPilotSignature {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$ExpectedThumbprint
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing signed pilot file: $Path" }
  $signature = Get-AuthenticodeSignature -LiteralPath $Path
  if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) {
    throw "Invalid Authenticode signature for $Path`: $($signature.Status) $($signature.StatusMessage)"
  }
  if ($signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
    throw "Unexpected signer for $Path. Expected certificate $ExpectedThumbprint."
  }
}

$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$preflight = Join-Path $source 'Preflight.ps1'
$powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
& $powerShell -NoProfile -ExecutionPolicy AllSigned -File $preflight -Server $Server
if ($LASTEXITCODE -ne 0) { throw 'Windows installation preflight failed.' }
$destination = Join-Path $env:ProgramFiles 'HospitalRemoteAgent'
$osVersion = [Version](Get-WmiObject Win32_OperatingSystem).Version
$isLegacyOS = $osVersion.Major -lt 10
if ($isLegacyOS -and -not $Legacy) { throw 'Windows 7/8/8.1 requires -Legacy and a Go 1.20-built legacy artifact.' }
$architecture = if ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64' -or $env:PROCESSOR_ARCHITECTURE -eq 'AMD64') { 'amd64' } else { '386' }
$flavor = if ($Legacy) { "legacy-$architecture" } else { 'modern-amd64' }
$agentBinary = Join-Path $source "build\$flavor\hospital-remote-agent.exe"
$uiScript = Join-Path $source 'ui\HospitalRemoteUI.ps1'
$installerSignature = Get-AuthenticodeSignature -LiteralPath $MyInvocation.MyCommand.Path
if ($installerSignature.Status -ne 'Valid' -or -not $installerSignature.SignerCertificate) {
  throw "The installer signature is not valid: $($installerSignature.Status) $($installerSignature.StatusMessage)"
}
$expectedThumbprint = $installerSignature.SignerCertificate.Thumbprint
Assert-ValidPilotSignature -Path $agentBinary -ExpectedThumbprint $expectedThumbprint
Assert-ValidPilotSignature -Path $uiScript -ExpectedThumbprint $expectedThumbprint
$account = New-Object Security.Principal.NTAccount $UIUser
$sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item $agentBinary (Join-Path $destination 'hospital-remote-agent.exe') -Force
Copy-Item $uiScript $destination -Force

Write-Host 'The agent will show a verification code. Approve the matching code on the Diagnostic Machines page.'
& (Join-Path $destination 'hospital-remote-agent.exe') install --server $Server --machine $MachineCode --ui-user $UIUser
if ($LASTEXITCODE -ne 0) { throw 'Agent service installation failed.' }

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
$action.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy AllSigned -File `"$destination\HospitalRemoteUI.ps1`""
$action.WorkingDirectory = $destination
$task.Settings.RestartCount = 5
$task.Settings.RestartInterval = 'PT1M'
$task.Settings.ExecutionTimeLimit = 'PT0S'
$taskFolder.RegisterTaskDefinition('Hospital Remote Notification UI', $task, 6, $UIUser, $null, 3, $null) | Out-Null
if ($identity.User.Value -eq $sid) {
	$taskFolder.GetTask('Hospital Remote Notification UI').Run($null) | Out-Null
}
Write-Host 'Hospital Remote Agent installed. The notification application starts at the UI user sign-in and restarts independently after failure.'

# SIG # Begin signature block
# MIIHTgYJKoZIhvcNAQcCoIIHPzCCBzsCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCAPiGoDttqgP2Pm
# dhDwgIik1iPJsuKCij6jPhptXaYdrKCCBDAwggQsMIIClKADAgECAhA33nkWJxw9
# pUTEsQCq7EsBMA0GCSqGSIb3DQEBCwUAMC4xLDAqBgNVBAMMI0hvc3BpdGFsIERp
# YWdub3N0aWMgRGFzaGJvYXJkIFBpbG90MB4XDTI2MTAyNzExNTQ0MVoXDTI4MTAy
# NzEyMDQ0MlowLjEsMCoGA1UEAwwjSG9zcGl0YWwgRGlhZ25vc3RpYyBEYXNoYm9h
# cmQgUGlsb3QwggGiMA0GCSqGSIb3DQEBAQUAA4IBjwAwggGKAoIBgQCVtwLIHQhm
# FR8ah1ZrDRRgt9ods2OaAlpXR0UdfTLxJv/ghJGQT9xE+VGFM/mF+RZiACn0eXF5
# ozg44ygDktpilsVgageWwa7N2AjPNvtZUIWbfmiD2GoIDC5V5FLrSMedB2TMnVt2
# LKN4a0zqletP+oE16VZLCPB2t8FE+yVotWrDMwaPuDkwm+OK2WkpBZRV5vet036r
# elf1sEobUrO3+M8v11COcpnoq8KKP4HQ4iqmP1wz+YkryiJlfn6LoF3KVFJFsm2Q
# j9qXuH4NRwKRTKtPPfrrKDIecibCyzCVLzM1yrO0+jgeX4mWSYm+zZu0gOGNi4rM
# XqaO6z6HebjnAtZTXNShwH/PQOY70UWj5dpvjiHpbNBP5HwkqW95n/X2/q+rDtoO
# B8wMe9ld/wg6CEvTxqBPe6RcOOuXOaTORoM4lP0ZR+mbTp2RA0XapXAgu8XVsvOt
# uI4+EMUAnmqucso+iEPMBGVh0TvwX1ytMJO1ZzivQbV9dHapKMmfYrUCAwEAAaNG
# MEQwDgYDVR0PAQH/BAQDAgeAMBMGA1UdJQQMMAoGCCsGAQUFBwMDMB0GA1UdDgQW
# BBQTyU45KVffaqkfDCByzUnLxcJT1zANBgkqhkiG9w0BAQsFAAOCAYEAZXUAlIsQ
# xYhF52VyGLAQD2UtZjqA+N4z6UfgsMQZa2DTAcN08aN9t+PAXN0udeMaGdeIlAdw
# DOb4OssBT2dTsNNsiZQkcTUiVz+NmLaAWGX9sPhY3xqf6heJL/Zc9otNU74c2I/w
# ogr8qizI4HdtHpUxn6ILaLO7qkB/AQe6AuHS6NFMdPADE5EvtECLmvsHnMjpvEz+
# rFM1kH1kloi926zdfEkUaVgKk55ttXhYrQkhpoudGyEy1R0q3hil5MbsaC82i2/g
# P1ug24XknZNi4C/CzcPISR/VGQnZUYznyal1pWFxOW4BBnOxd1uYIxfOhZ5xfgne
# pIJVbjtBa9++pXb2qWAHvittRcpNc5ISero3njzwN07ZiMFagGFYuhrwCjMWksib
# uHbCaAJLM+zaSqIGYrHimaTWQnqYsKJwFNYbpeG6U6C1huc2pki2H8ODEQ7kxqPk
# mPuc926c6a4wRQumwslEq3m9RhhDaqIw1MSt8/c7I1/qScn0OePNrQLpMYICdDCC
# AnACAQEwQjAuMSwwKgYDVQQDDCNIb3NwaXRhbCBEaWFnbm9zdGljIERhc2hib2Fy
# ZCBQaWxvdAIQN955FiccPaVExLEAquxLATANBglghkgBZQMEAgEFAKCBhDAYBgor
# BgEEAYI3AgEMMQowCKACgAChAoAAMBkGCSqGSIb3DQEJAzEMBgorBgEEAYI3AgEE
# MBwGCisGAQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCAf
# X/YptM6UgKqWPR+VMsWa285juhsc2UDHCmn03MrIzTANBgkqhkiG9w0BAQEFAASC
# AYAmN9Fz4d8pONxsnUK5sOWGhlEmb0s3tPZQEqD0TRGYWYiS6PCAOUP+LB5GN2fj
# zIqRR3xwghc0q2NeWzn78sFInYLAOmzsX56b6xC3xP2y1NiyjdSe1zP+ZZdStsVI
# VfgCxZgqFa1PW9JTQyrOqEMCb/fjlxgMHq9umHbNN/ua/KlZnxFY6rGc1Q2haZjK
# adZ+CRlZNZ3bs/WLMS0q//YrRY8hfdbt2kHMjv3oBtJ/Ax3zRO1Y7bgz/mrKvHfp
# r/LHnKRH4SSN+OSNaKdPOwPeRrz+LKQ+Oogjh9YNGh+Zkn8n3f/g6mKvxdPmH70h
# 8zVsX1oZdEZAb/gf/ooAFz4o7Dx/z/8bLCh8Ced+xuTCtPYKujkaXQpfrNp8OWzT
# I70Ko4GNCv96lDxA1gB4Z4rwE3OEFdr+iWve6bKn6jt7HpepwnNAhl/s6Ult40Rd
# OtriTh8EXWvcyoKbFSesidI2Yo5H3z9XQfOZDmENDbsdG4zL6KXnBoXQHnlVSJ8n
# OHc=
# SIG # End signature block
