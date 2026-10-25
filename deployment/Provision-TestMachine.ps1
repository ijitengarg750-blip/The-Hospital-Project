param(
  [Parameter(Mandatory=$true)][string]$DashboardUrl,
  [string]$MachineCode = 'SIRIUS-TEST-01',
  [string]$MachineName = 'Two-laptop diagnostic test PC',
  [string]$AdminUsername = 'admin'
)

$ErrorActionPreference = 'Stop'
$password = Read-Host 'Dashboard administrator password' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try { $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }

$loginBody = @{username=$AdminUsername; password=$plainPassword} | ConvertTo-Json
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/auth/login" -WebSession $session -ContentType 'application/json' -Body $loginBody | Out-Null
$plainPassword = $null
$machines = @(Invoke-RestMethod -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines" -WebSession $session)
$machine = $machines | Where-Object { $_.code -eq $MachineCode } | Select-Object -First 1
if (-not $machine) {
  $machine = Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines" -WebSession $session -ContentType 'application/json' -Body (@{code=$MachineCode; name=$MachineName; modality='Test'; location='Isolated test laptop'} | ConvertTo-Json)
}
$credential = Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines/$($machine.id)/agent-credentials" -WebSession $session
Write-Host "Machine ID: $($machine.id)"
Write-Host "Machine code: $($credential.machine_code)"
Write-Host 'One-time agent key (paste only into Install-Agent.ps1 on the test laptop):'
Write-Host $credential.agent_key
