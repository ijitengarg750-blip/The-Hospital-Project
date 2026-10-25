param(
  [string]$LegacyGo = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '.tools\go1.20.14\go\bin\go.exe')
)

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$build = Join-Path $source 'build'
$cache = Join-Path $source '.gocache'
$temp = Join-Path $source '.gotmp'
$modernGo = (Get-Command go.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $LegacyGo)) { throw 'Legacy Go 1.20.14 is missing. Run Bootstrap-Legacy-Go.ps1 first.' }
if ((& $LegacyGo version) -notmatch 'go1\.20\.14') { throw 'The legacy build must use exactly Go 1.20.14.' }
New-Item -ItemType Directory -Path $cache, $temp -Force | Out-Null

function Build-Target([string]$Go, [string]$Flavor, [string]$Architecture, [string]$ModFile = '') {
  $output = Join-Path $build $Flavor
  New-Item -ItemType Directory -Path $output -Force | Out-Null
  $oldGOOS = $env:GOOS; $oldGOARCH = $env:GOARCH; $oldCGO = $env:CGO_ENABLED
  try {
    $env:GOOS = 'windows'; $env:GOARCH = $Architecture; $env:CGO_ENABLED = '0'
    $modArgument = @()
    if ($ModFile) { $modArgument = @("-modfile=$ModFile") }
    & $Go build $modArgument -trimpath -buildvcs=false -o (Join-Path $output 'hospital-remote-agent.exe') .\cmd\hospital-remote-agent
    if ($LASTEXITCODE -ne 0) { throw "Agent build failed for $Flavor" }
    & $Go build $modArgument -trimpath -buildvcs=false -o (Join-Path $output 'windows-auth-poc.exe') .\cmd\windows-auth-poc
    if ($LASTEXITCODE -ne 0) { throw "Authentication PoC build failed for $Flavor" }
  } finally {
    $env:GOOS = $oldGOOS; $env:GOARCH = $oldGOARCH; $env:CGO_ENABLED = $oldCGO
  }
}
Push-Location $source
$oldGoCache = $env:GOCACHE
$oldGoTemp = $env:GOTMPDIR
try {
  $env:GOCACHE = $cache
  $env:GOTMPDIR = $temp
  & $modernGo test ./...
  if ($LASTEXITCODE -ne 0) { throw 'Modern Go tests failed.' }
  & $LegacyGo test '-modfile=go.legacy.mod' ./...
  if ($LASTEXITCODE -ne 0) { throw 'Go 1.20 compatibility tests failed.' }
  Build-Target $modernGo 'modern-amd64' 'amd64'
  Build-Target $LegacyGo 'legacy-amd64' 'amd64' 'go.legacy.mod'
  Build-Target $LegacyGo 'legacy-386' '386' 'go.legacy.mod'
  @('modern-amd64', 'legacy-amd64', 'legacy-386') | ForEach-Object { Get-ChildItem -Path (Join-Path $build $_) -Filter *.exe } | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash.ToLowerInvariant())  $($_.Path.Substring($build.Length + 1))" } |
    Set-Content -Encoding ASCII (Join-Path $build 'SHA256SUMS.txt')
} finally {
  $env:GOCACHE = $oldGoCache
  $env:GOTMPDIR = $oldGoTemp
  Pop-Location
}
