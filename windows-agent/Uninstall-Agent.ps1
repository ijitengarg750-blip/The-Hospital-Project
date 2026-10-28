param([switch]$RemoveConfiguration)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal $identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this uninstaller from an elevated PowerShell window.' }

$program = Join-Path $env:ProgramFiles 'HospitalRemoteAgent'
$data = Join-Path $env:ProgramData 'HospitalRemoteAgent'
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$taskFolder = $taskService.GetFolder('\')
try { $taskFolder.DeleteTask('Hospital Remote Notification UI', 0) } catch [System.Runtime.InteropServices.COMException] { if ($_.Exception.HResult -ne -2147024894) { throw } }
$agent = Join-Path $program 'hospital-remote-agent.exe'
if (Test-Path -LiteralPath $agent) { & $agent uninstall }
if (Test-Path -LiteralPath $program) { Remove-Item -LiteralPath $program -Recurse -Force }
if ($RemoveConfiguration) {
  if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force }
}
Write-Host 'Hospital Remote Agent and per-user notification task removed.'

# SIG # Begin signature block
# MIIHTgYJKoZIhvcNAQcCoIIHPzCCBzsCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCCsV6etgD6IwvDt
# 6cVkd2T8cjPbO9gDX1Nh3eLLGZ+gwKCCBDAwggQsMIIClKADAgECAhA33nkWJxw9
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
# MBwGCisGAQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCDl
# /AduHM5NAfkUMuBjyJSkegj3Z6C2lyGB0gJQP29aLjANBgkqhkiG9w0BAQEFAASC
# AYAqVZEWvxXeenGVDhjxWSycXO5qtx5uhclejqfknDMdRtA8ywBa6xwQA1yRx1L1
# OTaKiodKl0rA03vNwxkZ7Bzny8Zy5mx4BzmSCvLEUkJtpIvPg74UegfoUwlxOfBD
# t9OdNtIO5jNcdoDsjAsqvnILPHRuQ+6hO8VvUV92vKkQQoWbpzzZuHuaici1JfV2
# U3GT//dGnmarhDb7p3BpphcdIJP2HYCmRSt/RThBr00mxrVdvzRCFyilZ4ljz9Sj
# 4Ic7gakN4nFG7D79EMVWAmf+SFghXQyRjqtcTr+InP7yo6iAf8K74fHd2cxZu3Sz
# a1Svojbul186KlYO4GTwh+5dVEYtTe86+wSCtAEe79nXa2QgkUG2e/hvKPiqiImf
# 0Ky4Tq3PCOKkoMvT+ZcsgDDLF0BL6Bh5jE1aVrsr5oI++zVi6OOo4RaFDDfbONyL
# 1iIHmve32BcunfgdElBLjJKSfOXo6pHN6XcjgR7zisuJ0vXPwRE1u24SDn78Kch+
# 7vk=
# SIG # End signature block
