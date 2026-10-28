[CmdletBinding()]
param(
  [string]$Server,
  [string]$PackageRoot,
  [switch]$Json
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PackageRoot)) { $PackageRoot = $PSScriptRoot }

function New-PreflightResult {
  param(
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][ValidateSet('Passed', 'Warning', 'Failed', 'Not Tested')][string]$Status,
    [Parameter(Mandatory=$true)][string]$Message
  )
  [pscustomobject]@{ Name = $Name; Status = $Status; Message = $Message }
}
function Get-PlatformResult {
  param(
    [Version]$Version = [Environment]::OSVersion.Version,
    [string]$Architecture = $(if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE })
  )
  if ($Version.Major -ne 10) {
    return New-PreflightResult 'Windows platform' 'Failed' 'Windows 10 or 11 is required for the supported pilot. Older Windows remains a separate compatibility track.'
  }
  if ($Architecture -ne 'AMD64') {
    return New-PreflightResult 'Windows platform' 'Failed' 'The supported pilot requires x64 Windows.'
  }
  New-PreflightResult 'Windows platform' 'Passed' "Supported Windows x64 platform detected ($Version)."
}

function Get-LoopbackResult {
  param([string[]]$Addresses)
  if (-not $Addresses -or $Addresses.Count -eq 0) {
    return New-PreflightResult 'TightVNC loopback binding' 'Failed' 'Nothing is listening on TCP 5900.'
  }
  $nonLoopback = @($Addresses | Where-Object { $_ -notin @('127.0.0.1', '::1') })
  if ($nonLoopback.Count -gt 0) {
    return New-PreflightResult 'TightVNC loopback binding' 'Failed' 'TCP 5900 is exposed beyond loopback. Restrict the approved VNC server to this computer only.'
  }
  New-PreflightResult 'TightVNC loopback binding' 'Passed' 'TCP 5900 is listening only on loopback.'
}

function Get-InstallStateResult {
  param([bool]$ServiceExists, [bool]$BinaryExists, [bool]$ConfigExists)
  $present = @($ServiceExists, $BinaryExists, $ConfigExists | Where-Object { $_ }).Count
  if ($present -eq 0) {
    return New-PreflightResult 'Existing installation and enrollment' 'Passed' 'No existing Hospital Remote Agent installation or enrollment was found.'
  }
  if ($present -eq 3) {
    return New-PreflightResult 'Existing installation and enrollment' 'Warning' 'An existing enrolled installation was found. A later installer must preserve it during upgrade or repair.'
  }
  New-PreflightResult 'Existing installation and enrollment' 'Failed' 'A partial existing installation was found. Repair it without deleting enrollment data.'
}

function Get-SignatureResult {
  param([string]$Root)
  $paths = @(
    (Join-Path $Root 'Preflight.ps1'),
    (Join-Path $Root 'Install-Agent.ps1'),
    (Join-Path $Root 'Uninstall-Agent.ps1'),
    (Join-Path $Root 'ui\HospitalRemoteUI.ps1'),
    (Join-Path $Root 'build\modern-amd64\hospital-remote-agent.exe')
  )
  $thumbprint = $null
  foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      return New-PreflightResult 'Package signatures' 'Failed' "A required signed package file is missing: $([IO.Path]::GetFileName($path))."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) {
      return New-PreflightResult 'Package signatures' 'Failed' "A package signature is not trusted and valid: $([IO.Path]::GetFileName($path))."
    }
    if (-not $thumbprint) { $thumbprint = $signature.SignerCertificate.Thumbprint }
    if ($signature.SignerCertificate.Thumbprint -ne $thumbprint) {
      return New-PreflightResult 'Package signatures' 'Failed' 'Package files were not signed by the same certificate.'
    }
  }
  New-PreflightResult 'Package signatures' 'Passed' 'All installer and executable signatures are valid and use the same signer.'
}

function Get-AppControlResult {
  param([string]$AgentPath, [bool]$SignatureValid = $true)
  if (-not $SignatureValid) {
    return New-PreflightResult 'App Control execution' 'Not Tested' 'The execution probe was not run because package signature validation failed.'
  }
  if (-not (Test-Path -LiteralPath $AgentPath -PathType Leaf)) {
    return New-PreflightResult 'App Control execution' 'Not Tested' 'The agent executable is missing, so WDAC/App Control authorization could not be tested.'
  }
  try {
    & $AgentPath preflight 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      return New-PreflightResult 'App Control execution' 'Passed' 'The signed agent executable was authorized to run a side-effect-free probe.'
    }
    New-PreflightResult 'App Control execution' 'Failed' 'The signed agent preflight probe did not complete successfully.'
  } catch {
    New-PreflightResult 'App Control execution' 'Failed' 'Windows blocked or could not start the signed agent preflight probe.'
  }
}

function Get-NetworkResults {
  param([string]$DashboardServer)
  try { $uri = [Uri]$DashboardServer } catch {
    return (New-PreflightResult 'DNS resolution' 'Failed' 'The dashboard server URL is invalid.'),
      (New-PreflightResult 'HTTPS/WSS TLS on port 443' 'Failed' 'A valid HTTPS or WSS dashboard URL is required.')
  }
  if ($uri.Scheme -notin @('https', 'wss') -or $uri.Port -ne 443) {
    return (New-PreflightResult 'DNS resolution' 'Not Tested' 'DNS was not tested because the server URL is not an HTTPS/WSS port 443 URL.'),
      (New-PreflightResult 'HTTPS/WSS TLS on port 443' 'Failed' 'The dashboard must use HTTPS or WSS on port 443.')
  }
  try {
    $addresses = [Net.Dns]::GetHostAddresses($uri.DnsSafeHost)
    if ($addresses.Count -eq 0) { throw 'No address returned.' }
    $dns = New-PreflightResult 'DNS resolution' 'Passed' "Resolved $($uri.DnsSafeHost)."
  } catch {
    return (New-PreflightResult 'DNS resolution' 'Failed' "Could not resolve $($uri.DnsSafeHost)."),
      (New-PreflightResult 'HTTPS/WSS TLS on port 443' 'Not Tested' 'TLS was not tested because DNS resolution failed.')
  }

  $client = New-Object Net.Sockets.TcpClient
  $ssl = $null
  try {
    $connect = $client.BeginConnect($uri.DnsSafeHost, 443, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(5000)) { throw 'Connection timed out.' }
    $client.EndConnect($connect)
    $client.ReceiveTimeout = 5000
    $client.SendTimeout = 5000
    $ssl = New-Object Net.Security.SslStream($client.GetStream(), $false)
    $authenticate = $ssl.BeginAuthenticateAsClient($uri.DnsSafeHost, $null, $null)
    if (-not $authenticate.AsyncWaitHandle.WaitOne(5000)) { throw 'TLS handshake timed out.' }
    $ssl.EndAuthenticateAsClient($authenticate)
    $tls = New-PreflightResult 'HTTPS/WSS TLS on port 443' 'Passed' 'Port 443 is reachable and the dashboard TLS certificate is trusted.'
  } catch {
    $tls = New-PreflightResult 'HTTPS/WSS TLS on port 443' 'Failed' 'Could not establish a trusted TLS connection to the dashboard on port 443.'
  } finally {
    if ($ssl) { $ssl.Dispose() }
    $client.Dispose()
  }
  @($dns, $tls)
}

function Get-TightVNCResults {
  $service = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^tvnserver' -or $_.DisplayName -match 'TightVNC' } | Select-Object -First 1)
  if ($service.Count -eq 0) {
    $installed = New-PreflightResult 'TightVNC installation' 'Failed' 'An approved TightVNC service installation was not found.'
  } elseif ($service[0].Status -ne 'Running') {
    $installed = New-PreflightResult 'TightVNC installation' 'Failed' 'TightVNC is installed but its service is not running.'
  } else {
    $installed = New-PreflightResult 'TightVNC installation' 'Passed' 'The TightVNC service is installed and running.'
  }
  try {
    $addresses = @(Get-NetTCPConnection -State Listen -LocalPort 5900 -ErrorAction Stop | Select-Object -ExpandProperty LocalAddress -Unique)
    $loopback = Get-LoopbackResult $addresses
  } catch {
    $loopback = Get-LoopbackResult @()
  }
  $authentication = New-PreflightResult 'TightVNC authentication' 'Not Tested' 'Authentication requires the approved VNC credential and is tested after secure enrollment; an open port alone is not treated as proof.'
  @($installed, $loopback, $authentication)
}

function Get-FilesystemResult {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal $identity
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    return New-PreflightResult 'Filesystem permissions' 'Failed' 'Run preflight from an elevated administrator session so protected installation paths can be checked.'
  }
  foreach ($path in @((Join-Path $env:ProgramFiles 'HospitalRemoteAgent'), (Join-Path $env:ProgramData 'HospitalRemoteAgent'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { continue }
    $unsafe = @(Get-Acl -LiteralPath $path).Access | Where-Object {
      $_.AccessControlType -eq 'Allow' -and
      $_.IdentityReference.Value -match '(Everyone|BUILTIN\\Users|Authenticated Users)$' -and
      $_.FileSystemRights.ToString() -match 'Write|Modify|FullControl'
    }
    if ($unsafe) {
      return New-PreflightResult 'Filesystem permissions' 'Failed' 'An existing agent directory grants write access to a broad user group.'
    }
  }
  New-PreflightResult 'Filesystem permissions' 'Passed' 'Administrator access is available and existing agent directories do not grant broad write access.'
}

function Get-ServiceConfigurationResult {
  param([string]$ExpectedBinary)
  $service = Get-CimInstance Win32_Service -Filter "Name='HospitalRemoteAgent'" -ErrorAction SilentlyContinue
  if (-not $service) {
    return New-PreflightResult 'Service configuration and dependencies' 'Not Tested' 'The connector service is not installed; its final configuration will be checked after installation.'
  }
  $valid = $service.StartMode -eq 'Auto' -and $service.StartName -eq 'LocalSystem' -and $service.PathName -like "*$ExpectedBinary*"
  if (-not $valid) {
    return New-PreflightResult 'Service configuration and dependencies' 'Failed' 'The existing connector service is not automatic, LocalSystem, or using the expected protected executable.'
  }
  $dependencies = @(Get-Service -Name 'HospitalRemoteAgent' -ErrorAction SilentlyContinue).ServicesDependedOn
  if ($dependencies.Count -gt 0) {
    return New-PreflightResult 'Service configuration and dependencies' 'Warning' 'The connector has additional Windows service dependencies; review them before upgrade.'
  }
  New-PreflightResult 'Service configuration and dependencies' 'Passed' 'The connector is automatic, runs as LocalSystem, and has no added service dependency.'
}

function Get-PreflightExitCode {
  param([object[]]$Results)
  if (@($Results | Where-Object { $_.Status -eq 'Failed' }).Count -gt 0) { return 1 }
  0
}

function Invoke-HospitalPreflight {
  param([Parameter(Mandatory=$true)][string]$DashboardServer, [string]$Root = $PSScriptRoot)
  $program = Join-Path $env:ProgramFiles 'HospitalRemoteAgent\hospital-remote-agent.exe'
  $data = Join-Path $env:ProgramData 'HospitalRemoteAgent\service.dat'
  $service = Get-Service -Name 'HospitalRemoteAgent' -ErrorAction SilentlyContinue
  $packageAgent = Join-Path $Root 'build\modern-amd64\hospital-remote-agent.exe'
  $signature = Get-SignatureResult $Root
  @(
    Get-PlatformResult
    $signature
    Get-AppControlResult $packageAgent ($signature.Status -eq 'Passed')
    Get-NetworkResults $DashboardServer
    Get-InstallStateResult ([bool]$service) (Test-Path -LiteralPath $program -PathType Leaf) (Test-Path -LiteralPath $data -PathType Leaf)
    Get-TightVNCResults
    Get-FilesystemResult
    Get-ServiceConfigurationResult $program
  )
}

if ($MyInvocation.InvocationName -ne '.') {
  if ([string]::IsNullOrWhiteSpace($Server)) { throw 'Server is required.' }
  $results = @(Invoke-HospitalPreflight -DashboardServer $Server -Root $PackageRoot)
  if ($Json) { $results | ConvertTo-Json -Depth 3 } else { $results | Format-Table -AutoSize | Out-Host }
  exit (Get-PreflightExitCode $results)
}
