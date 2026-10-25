param()

$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $source '.tools'
$archive = Join-Path $tools 'go1.20.14.windows-amd64.zip'
$destination = Join-Path $tools 'go1.20.14'
$url = 'https://go.dev/dl/go1.20.14.windows-amd64.zip'
$expectedSha256 = '0e0d0190406ead891d94ecf00f961bb5cfa15ddd47499d2649f12eee80aee110'

New-Item -ItemType Directory -Path $tools -Force | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
}
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actual -ne $expectedSha256) { throw "Go archive SHA-256 mismatch: $actual" }
if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
New-Item -ItemType Directory -Path $destination | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $destination
& (Join-Path $destination 'go\bin\go.exe') version
