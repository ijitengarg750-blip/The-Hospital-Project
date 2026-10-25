param(
  [Parameter(Mandatory=$true)][string]$DashboardUrl,
  [string]$MachineCode = 'SIRIUS-TEST-01',
  [string]$MachineName = 'Two-laptop diagnostic test PC',
  [string]$DoctorUsername = 'doctor'
)

$ErrorActionPreference = 'Stop'
$password = Read-Host 'Dashboard doctor password' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try { $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }

$loginBody = @{username=$DoctorUsername; password=$plainPassword} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/auth/login" -ContentType 'application/json' -Body $loginBody
$plainPassword = $null
$headers = @{Authorization="Bearer $($login.access_token)"}
$machines = @(Invoke-RestMethod -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines" -Headers $headers)
$machine = $machines | Where-Object { $_.code -eq $MachineCode } | Select-Object -First 1
if (-not $machine) {
  $machine = Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines" -Headers $headers -ContentType 'application/json' -Body (@{code=$MachineCode; name=$MachineName; modality='Test'; location='Isolated test laptop'} | ConvertTo-Json)
}
$credential = Invoke-RestMethod -Method Post -Uri "$($DashboardUrl.TrimEnd('/'))/api/machines/$($machine.id)/agent-credentials" -Headers $headers
Write-Host "Machine ID: $($machine.id)"
Write-Host "Machine code: $($credential.machine_code)"
Write-Host 'One-time agent key (paste only into Install-Agent.ps1 on the test laptop):'
Write-Host $credential.agent_key
