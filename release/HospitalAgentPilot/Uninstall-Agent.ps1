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
  if ([IO.Path]::GetFileName($launcher) -ne 'HospitalRemoteUI.lnk' -or (Split-Path -Parent $launcher) -notlike '*\Microsoft\Windows\Start Menu\Programs\Startup') { throw 'Refusing to remove an unexpected UI launcher path.' }
  if (Test-Path -LiteralPath $launcher) { Remove-Item -LiteralPath $launcher -Force }
}
$agent = Join-Path $program 'hospital-remote-agent.exe'
if (Test-Path -LiteralPath $agent) { & $agent uninstall }
if (Test-Path -LiteralPath $program) { Remove-Item -LiteralPath $program -Recurse -Force }
if ($RemoveConfiguration) {
  if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force }
}
Write-Host 'Hospital Remote Agent and per-user notification launcher removed.'

# SIG # Begin signature block
# MIIHTgYJKoZIhvcNAQcCoIIHPzCCBzsCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCDijmtnFe2Y92HL
# bZDVDG1PwPSTelH+OwYTWWezZaOsDqCCBDAwggQsMIIClKADAgECAhA33nkWJxw9
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
# MBwGCisGAQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCBn
# 42jh+fcRUqyBxoBcU0rQtMJYjCSoBz8PXUUqK6fweDANBgkqhkiG9w0BAQEFAASC
# AYA/8AG0uKbsdDM/YcHFww2yketTs+PKcI9VrBIar3jurCYXgwHKpjOwicHBintV
# dH4grFGPloMubY59IbtIvrn2cOj/yT+FPPglHQcMNKqzt2kbxAA9Xw+mfScZ0C4J
# zITcaQfpNG9QhfzXBKdixzLo0D2Co2fSb7o8GZww5ogLfTjjdt2DBsmzIfpF6e+W
# SszaVUaqvBCXanomEcvQlI4GQmYOAXI0pe7D8tOerie+uHkKXqJ63XtTBY3Q+cLS
# Kd+uC8syHaF9MhibrLxUiz4FLvjS9qM3V93Wqlkr+0vxDolriGN8L+wxFBdWwHRE
# osRyVF5TGk6Gx4vaX5LU7i6ZHkeYuk8YkGGuxOc9PDl30CEHFmieZ6GrbP60G2+V
# myXlwyG9tfWa9bCw8pbp//7DikUUIeKOm61Ao8aZnZY669WRcTseVQ/vLz3ikj5q
# UZRIHv7KfXEhwbKuQke36TOvGwA1mb/Ea4eIkCDuXURdn2RTdj4xf0sN39BdB968
# 3+U=
# SIG # End signature block
