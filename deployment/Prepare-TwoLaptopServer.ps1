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

$utf8 = New-Object Text.UTF8Encoding $false

$lines = [Collections.Generic.List[string]](Get-Content -LiteralPath $envFile)
function Set-EnvValue([string]$Name, [string]$Value) {
  for ($index = 0; $index -lt $lines.Count; $index++) {
    if ($lines[$index] -match "^$([Regex]::Escape($Name))=") { $lines[$index] = "$Name=$Value"; return }
  }
  $lines.Add("$Name=$Value")
}
Set-EnvValue 'ALLOWED_ORIGINS' "https://$ServerIP`:8443"
Set-EnvValue 'VNC_GATEWAY_CA_CERT_FILE' '/run/mtls/gateway-ca.crt'
Set-EnvValue 'VNC_GATEWAY_CLIENT_CERT_FILE' '/run/mtls/gateway-client.crt'
Set-EnvValue 'VNC_GATEWAY_CLIENT_KEY_FILE' '/run/mtls/gateway-client.key'
[IO.File]::WriteAllLines($envFile, $lines, $utf8)

& (Join-Path $repository 'deployment\New-TestTlsCertificate.ps1') -ServerIP $ServerIP
if ($LASTEXITCODE -ne 0) { throw 'TLS certificate generation failed.' }
Write-Host "Server test configuration prepared for $MachineCode at $TestPcIP."
Write-Host 'No VNC credential is stored on the server. The diagnostic-PC agent keeps it locally under DPAPI.'
