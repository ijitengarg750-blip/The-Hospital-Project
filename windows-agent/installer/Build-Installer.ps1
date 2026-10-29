param(
  [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '0.1.0',
  [string]$OutputDirectory,
  [string]$WixPath
)

$ErrorActionPreference = 'Stop'
$wixVersion = '4.0.6'
$installerRoot = $PSScriptRoot
$agentRoot = Split-Path -Parent $installerRoot
$repoRoot = Split-Path -Parent $agentRoot
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $installerRoot 'out' }
if ([string]::IsNullOrWhiteSpace($WixPath)) {
  $wix = Get-Command wix.exe -ErrorAction SilentlyContinue
  if (-not $wix) { $wix = Get-Command wix -ErrorAction SilentlyContinue }
  if ($wix) { $WixPath = $wix.Source }
}
if (-not $WixPath -or -not (Test-Path -LiteralPath $WixPath -PathType Leaf)) {
  throw "WiX $wixVersion is required. Install that pinned wix.exe tool explicitly; this script does not change the machine toolchain."
}
$installedWixVersion = (& $WixPath --version).Trim()
if ($installedWixVersion -notmatch '^4\.0\.6(?:\+.*)?$') { throw "Expected WiX $wixVersion but found $installedWixVersion at $WixPath." }

$workingRoot = Join-Path ([IO.Path]::GetTempPath()) ("hospital-msi-" + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $workingRoot 'payload'
$goCache = Join-Path $workingRoot 'gocache'
$goTemp = Join-Path $workingRoot 'gotmp'
New-Item -ItemType Directory -Path $payload, $goCache, $goTemp, $OutputDirectory -Force | Out-Null
$oldGoCache = $env:GOCACHE
$oldGoTemp = $env:GOTMPDIR
try {
  $env:GOCACHE = $goCache
  $env:GOTMPDIR = $goTemp

  Push-Location $agentRoot
  try {
    & go.exe test ./...
    if ($LASTEXITCODE -ne 0) { throw 'Windows agent tests failed.' }
    & go.exe build -trimpath -o (Join-Path $payload 'hospital-remote-agent.exe') ./cmd/hospital-remote-agent
    if ($LASTEXITCODE -ne 0) { throw 'Windows agent build failed.' }
    & go.exe build -trimpath -buildvcs=false -o (Join-Path $payload 'hospital-remote-agent.exe') .\cmd\hospital-remote-agent
    if ($LASTEXITCODE -ne 0) { throw 'Windows agent build failed.' }
  } finally { Pop-Location }

  Push-Location (Join-Path $repoRoot 'collector')
  try {
    & go.exe test ./...
    if ($LASTEXITCODE -ne 0) { throw 'Collector tests failed.' }
    & go.exe build -trimpath -o (Join-Path $payload 'hospital-collector.exe') .
    if ($LASTEXITCODE -ne 0) { throw 'Collector build failed.' }
    & go.exe build -trimpath -buildvcs=false -o (Join-Path $payload 'hospital-collector.exe') .
    if ($LASTEXITCODE -ne 0) { throw 'Collector build failed.' }
  } finally { Pop-Location }

  $payloadFiles = @{
    'Install-Agent.ps1' = (Join-Path $agentRoot 'Install-Agent.ps1')
    'Preflight.ps1' = (Join-Path $agentRoot 'Preflight.ps1')
    'Register-NotificationUI.ps1' = (Join-Path $agentRoot 'Register-NotificationUI.ps1')
    'Uninstall-Agent.ps1' = (Join-Path $agentRoot 'Uninstall-Agent.ps1')
    'HospitalRemoteUI.ps1' = (Join-Path $agentRoot 'ui\HospitalRemoteUI.ps1')
    'install-collector-task.ps1' = (Join-Path $repoRoot 'deployment\install-collector-task.ps1')
    'Configure-Installation.ps1' = (Join-Path $installerRoot 'Configure-Installation.ps1')
    'Remove-Installation.ps1' = (Join-Path $installerRoot 'Remove-Installation.ps1')
  }
  foreach ($name in $payloadFiles.Keys) { Copy-Item -LiteralPath $payloadFiles[$name] -Destination (Join-Path $payload $name) }

  $output = Join-Path $OutputDirectory "HospitalDiagnosticComponents-$Version-x64.msi"
  & $WixPath build (Join-Path $installerRoot 'Product.wxs') -arch x64 `
    -d "PayloadDir=$payload" -d "ProductVersion=$Version" `
    -ext "WixToolset.UI.wixext/$wixVersion" -ext "WixToolset.Util.wixext/$wixVersion" `
    -out $output
  if ($LASTEXITCODE -ne 0) { throw 'WiX MSI build failed.' }
  Write-Output $output
}
finally {
  $env:GOCACHE = $oldGoCache
  $env:GOTMPDIR = $oldGoTemp
  if (Test-Path -LiteralPath $workingRoot) { Remove-Item -LiteralPath $workingRoot -Recurse -Force }
}
