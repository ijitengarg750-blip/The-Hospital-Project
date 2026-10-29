param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[A-Fa-f0-9]{40}$')][string]$CertificateThumbprint,
  [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $source
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
  $OutputDirectory = Join-Path $repoRoot 'release\HospitalAgentPilot'
}
$certificate = Get-ChildItem "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction Stop
if (-not $certificate.HasPrivateKey) { throw 'The signing certificate does not have an accessible private key.' }
if (-not ($certificate.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3')) {
  throw 'The selected certificate is not authorized for code signing.'
}

$signTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction Stop |
  Sort-Object FullName -Descending |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $signTool) { throw 'SignTool was not found in the Windows 10 SDK.' }

$publicCertificatePath = Join-Path $repoRoot 'release\HospitalDashboardPilot.cer'
$publicCertificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2 $publicCertificatePath
if ($publicCertificate.Thumbprint -ne $CertificateThumbprint) {
  throw 'HospitalDashboardPilot.cer does not match the selected signing certificate.'
}

$agentOutput = Join-Path $source 'build\modern-amd64\hospital-remote-agent.exe'
$goCache = Join-Path $source '.gocache'
$goTemp = Join-Path $source '.gotmp'
New-Item -ItemType Directory -Path $goCache, $goTemp -Force | Out-Null

Push-Location $source
$oldGoCache = $env:GOCACHE
$oldGoTemp = $env:GOTMPDIR
try {
  $env:GOCACHE = $goCache
  $env:GOTMPDIR = $goTemp
  & go.exe test ./...
  if ($LASTEXITCODE -ne 0) { throw 'Windows agent tests failed.' }
  & go.exe build -trimpath -buildvcs=false -o $agentOutput .\cmd\hospital-remote-agent
  if ($LASTEXITCODE -ne 0) { throw 'Modern Windows agent build failed.' }
} finally {
  $env:GOCACHE = $oldGoCache
  $env:GOTMPDIR = $oldGoTemp
  Pop-Location
}

$buildDirectory = Join-Path $OutputDirectory 'build\modern-amd64'
$uiDirectory = Join-Path $OutputDirectory 'ui'
New-Item -ItemType Directory -Path $OutputDirectory, $buildDirectory, $uiDirectory -Force | Out-Null

Copy-Item -LiteralPath $agentOutput -Destination (Join-Path $buildDirectory 'hospital-remote-agent.exe') -Force
Copy-Item -LiteralPath (Join-Path $source 'Install-Agent.ps1') -Destination (Join-Path $OutputDirectory 'Install-Agent.ps1') -Force
Copy-Item -LiteralPath (Join-Path $source 'Uninstall-Agent.ps1') -Destination (Join-Path $OutputDirectory 'Uninstall-Agent.ps1') -Force
Copy-Item -LiteralPath (Join-Path $source 'Preflight.ps1') -Destination (Join-Path $OutputDirectory 'Preflight.ps1') -Force
Copy-Item -LiteralPath (Join-Path $source 'Register-NotificationUI.ps1') -Destination (Join-Path $OutputDirectory 'Register-NotificationUI.ps1') -Force
Copy-Item -LiteralPath (Join-Path $source 'ui\HospitalRemoteUI.ps1') -Destination (Join-Path $uiDirectory 'HospitalRemoteUI.ps1') -Force
Copy-Item -LiteralPath $publicCertificatePath -Destination (Join-Path $OutputDirectory 'HospitalDashboardPilot.cer') -Force
Copy-Item -LiteralPath (Join-Path $source 'PILOT-TEST.md') -Destination (Join-Path $OutputDirectory 'README.md') -Force

$agent = Join-Path $buildDirectory 'hospital-remote-agent.exe'
& $signTool sign /sha1 $CertificateThumbprint /s My /fd SHA256 /d 'Hospital Diagnostic Dashboard Pilot' $agent
if ($LASTEXITCODE -ne 0) { throw 'Executable signing failed.' }

$scripts = @(
  (Join-Path $OutputDirectory 'Install-Agent.ps1'),
  (Join-Path $OutputDirectory 'Uninstall-Agent.ps1'),
  (Join-Path $OutputDirectory 'Preflight.ps1'),
  (Join-Path $OutputDirectory 'Register-NotificationUI.ps1'),
  (Join-Path $uiDirectory 'HospitalRemoteUI.ps1')
)
foreach ($script in $scripts) {
  $result = Set-AuthenticodeSignature -LiteralPath $script -Certificate $certificate -HashAlgorithm SHA256
  if ($result.Status -ne 'Valid') { throw "Script signing failed for $script`: $($result.StatusMessage)" }
}

$packageFiles = @($agent) + $scripts
foreach ($file in $packageFiles) {
  $signature = Get-AuthenticodeSignature -LiteralPath $file
  if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
    throw "Signature verification failed for $file."
  }
}

$manifest = foreach ($file in $packageFiles) {
  $hash = Get-FileHash -LiteralPath $file -Algorithm SHA256
  $outputRoot = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\') + '\'
  $relative = [IO.Path]::GetFullPath($file).Substring($outputRoot.Length)
  "$($hash.Hash.ToLowerInvariant())  $relative"
}
$manifest | Set-Content -LiteralPath (Join-Path $OutputDirectory 'SHA256SUMS.txt') -Encoding ASCII

Write-Host "Verified pilot package created at: $OutputDirectory"
Get-AuthenticodeSignature -LiteralPath $packageFiles |
  Select-Object Path, Status, @{Name='Thumbprint'; Expression={$_.SignerCertificate.Thumbprint}} |
  Format-Table -AutoSize
