param(
  [Parameter(Mandatory=$true)][string]$ServerIP,
  [Parameter(Mandatory=$true)][string]$TestPcIP,
  [string]$MachineCode = 'SIRIUS-TEST-01'
)

$ErrorActionPreference = 'Stop'
$parsed = $null
if (-not [Net.IPAddress]::TryParse($ServerIP, [ref]$parsed)) { throw 'ServerIP must be an IP address assigned to the Docker laptop.' }
if (-not [Net.IPAddress]::TryParse($TestPcIP, [ref]$parsed)) { throw 'TestPcIP must be the diagnostic test laptop IP address.' }
$repository = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envFile = Join-Path $repository '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw '.env is missing. Run python setup-local.py --init first.' }

$secretDirectory = Join-Path $repository 'deployment\test-secrets'
New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
$passwordFile = Join-Path $secretDirectory 'vnc-password.txt'
$secure = Read-Host 'Enter the same 1-to-8 character TEST password that you will configure in TightVNC' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $vncPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
  if ($vncPassword.Length -lt 1 -or $vncPassword.Length -gt 8) { throw 'Classic VNC requires a 1-to-8 character password.' }
  [IO.File]::WriteAllText($passwordFile, $vncPassword, [Text.Encoding]::GetEncoding(28591))
  $vncPassword = $null
} finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
icacls $passwordFile /inheritance:r /grant:r "$env:USERNAME`:F" | Out-Null

$target = @{}
$target[$MachineCode] = @{host=$TestPcIP; port=5900; password_file='/run/secrets/sirius_vnc_password'}
$utf8 = New-Object Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $repository 'deployment\vnc-targets.json'), ($target | ConvertTo-Json -Depth 3), $utf8)

$lines = [Collections.Generic.List[string]](Get-Content -LiteralPath $envFile)
function Set-EnvValue([string]$Name, [string]$Value) {
  for ($index = 0; $index -lt $lines.Count; $index++) {
    if ($lines[$index] -match "^$([Regex]::Escape($Name))=") { $lines[$index] = "$Name=$Value"; return }
  }
  $lines.Add("$Name=$Value")
}
Set-EnvValue 'ALLOWED_ORIGINS' "https://$ServerIP`:8443"
Set-EnvValue 'SIRIUS_VNC_PASSWORD_FILE' $passwordFile
[IO.File]::WriteAllLines($envFile, $lines, $utf8)

& (Join-Path $repository 'deployment\New-TestTlsCertificate.ps1') -ServerIP $ServerIP
if ($LASTEXITCODE -ne 0) { throw 'TLS certificate generation failed.' }
Write-Host "Server test configuration prepared for $MachineCode at $TestPcIP."
Write-Host 'The VNC password was not printed. Enter the same test value in TightVNC service configuration.'
