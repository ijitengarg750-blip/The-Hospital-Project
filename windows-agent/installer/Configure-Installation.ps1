param([switch]$NoRun)

$ErrorActionPreference = 'Stop'

function Test-DashboardServer {
  param([string]$Server)
  try { $uri = [uri]$Server } catch { return $false }
  if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or -not $uri.Host -or $uri.Port -ne 443) { return $false }
  if (-not [string]::IsNullOrEmpty($uri.UserInfo) -or -not [string]::IsNullOrEmpty($uri.Fragment) -or -not [string]::IsNullOrEmpty($uri.Query)) { return $false }
  return $true
}

function New-CollectorConfiguration {
  param(
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$MachineCode,
    [Parameter(Mandatory=$true)][string]$WatchFolder
  )
  [ordered]@{
    machine_code = $MachineCode
    server_url = $Server.TrimEnd('/')
    collector_credential_file = 'collector-credential.dat'
    watch_folders = @($WatchFolder)
    extensions = @('.dcm')
    practo_uid_regex = ''
    study_uid_regex = ''
    study_title = 'Pupillography'
    poll_seconds = 10
    stable_checks = 3
    state_file = 'state.json'
    health_file = 'health.json'
  } | ConvertTo-Json
}

function Invoke-WithSecureInput {
  param(
    [Parameter(Mandatory=$true)][string]$FilePath,
    [Parameter(Mandatory=$true)][string]$Arguments,
    [Parameter(Mandatory=$true)][Security.SecureString]$Secret
  )
  $start = New-Object Diagnostics.ProcessStartInfo
  $start.FileName = $FilePath
  $start.Arguments = $Arguments
  $start.UseShellExecute = $false
  $start.RedirectStandardInput = $true
  $start.CreateNoWindow = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $start
  $pointer = [IntPtr]::Zero
  try {
    if (-not $process.Start()) { throw "Could not start $FilePath." }
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    $process.StandardInput.WriteLine([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "$([IO.Path]::GetFileName($FilePath)) failed with exit code $($process.ExitCode)." }
  }
  finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $process.Dispose()
  }
}

if ($NoRun) { return }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal $identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
  $process = Start-Process -FilePath $powerShell -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'AllSigned', '-File', "`"$($MyInvocation.MyCommand.Path)`"")
  exit $process.ExitCode
}

$form = New-Object Windows.Forms.Form
$form.Text = 'Configure Hospital Diagnostic Components'
$form.ClientSize = New-Object Drawing.Size(650, 560)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$fields = [ordered]@{
  'Dashboard HTTPS URL' = 'https://'
  'Diagnostic machine code' = ''
  'Notification Windows user' = "$env:USERDOMAIN\$env:USERNAME"
  'DICOM export folder' = ''
  'Collector service account' = '.\HospitalCollectorSvc'
  'Local TightVNC password' = ''
  'Collector credential' = ''
}
$controls = @{}
$top = 20
foreach ($labelText in $fields.Keys) {
  $label = New-Object Windows.Forms.Label
  $label.Text = $labelText
  $label.Location = New-Object Drawing.Point(20, $top)
  $label.Size = New-Object Drawing.Size(210, 24)
  $form.Controls.Add($label)
  $box = New-Object Windows.Forms.TextBox
  $box.Text = $fields[$labelText]
  $box.Location = New-Object Drawing.Point(235, $top)
  $box.Size = New-Object Drawing.Size(390, 24)
  if ($labelText -in @('Local TightVNC password', 'Collector credential')) { $box.UseSystemPasswordChar = $true }
  $controls[$labelText] = $box
  $form.Controls.Add($box)
  $top += 42
}

$status = New-Object Windows.Forms.TextBox
$status.Location = New-Object Drawing.Point(20, 320)
$status.Size = New-Object Drawing.Size(605, 170)
$status.Multiline = $true
$status.ReadOnly = $true
$status.ScrollBars = 'Vertical'
$form.Controls.Add($status)

$install = New-Object Windows.Forms.Button
$install.Text = 'Configure components'
$install.Location = New-Object Drawing.Point(390, 510)
$install.Size = New-Object Drawing.Size(140, 30)
$form.Controls.Add($install)
$close = New-Object Windows.Forms.Button
$close.Text = 'Close'
$close.Location = New-Object Drawing.Point(540, 510)
$close.Size = New-Object Drawing.Size(85, 30)
$close.Add_Click({ $form.Close() })
$form.Controls.Add($close)

$install.Add_Click({
  $server = $controls['Dashboard HTTPS URL'].Text.Trim()
  $machine = $controls['Diagnostic machine code'].Text.Trim()
  $uiUser = $controls['Notification Windows user'].Text.Trim()
  $watchFolder = $controls['DICOM export folder'].Text.Trim()
  $collectorUser = $controls['Collector service account'].Text.Trim()
  if (-not (Test-DashboardServer $server)) { [Windows.Forms.MessageBox]::Show('Enter an HTTPS URL on port 443 without credentials or secret query values.') | Out-Null; return }
  if ($machine -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { [Windows.Forms.MessageBox]::Show('Enter a valid diagnostic machine code.') | Out-Null; return }
  if (-not $uiUser -or -not $collectorUser -or -not (Test-Path -LiteralPath $watchFolder -PathType Container)) { [Windows.Forms.MessageBox]::Show('Windows users and an existing DICOM export folder are required.') | Out-Null; return }
  try {
    (New-Object Security.Principal.NTAccount($uiUser)).Translate([Security.Principal.SecurityIdentifier]) | Out-Null
    (New-Object Security.Principal.NTAccount($collectorUser)).Translate([Security.Principal.SecurityIdentifier]) | Out-Null
  } catch { [Windows.Forms.MessageBox]::Show('Enter existing local or domain accounts for notification and collection.') | Out-Null; return }

  $installRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
  $collectorData = Join-Path $env:ProgramData 'HospitalDataCollector'
  $connectorData = Join-Path $env:ProgramData 'HospitalRemoteAgent\service.dat'
  $components = @(
    @{ Name = 'Remote Connector'; Install = {
      if (Test-Path -LiteralPath $connectorData) {
        & (Join-Path $installRoot 'hospital-remote-agent.exe') repair-service $connectorData
        if ($LASTEXITCODE -ne 0) { throw 'Remote Connector service repair failed; enrollment was left unchanged.' }
        & sc.exe failure HospitalRemoteAgent reset= 86400 actions= restart/5000/restart/15000/restart/30000 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Remote Connector recovery configuration failed.' }
        return 'Existing enrollment retained and service repaired.'
      }
      if ($controls['Local TightVNC password'].TextLength -lt 1 -or [Text.Encoding]::UTF8.GetByteCount($controls['Local TightVNC password'].Text) -gt 8) { throw 'The local TightVNC password must be 1 to 8 bytes.' }
      & (Join-Path $installRoot 'Install-Agent.ps1') -Server $server -MachineCode $machine -UIUser $uiUser -InstalledLayout -SkipNotificationUI -VNCPassword $controls['Local TightVNC password'].SecurePassword
      if ($LASTEXITCODE -ne 0) { throw 'Remote Connector installation failed.' }
      'Installed and enrolled.'
    }}
    @{ Name = 'Local Notification UI'; Install = {
      & (Join-Path $installRoot 'Register-NotificationUI.ps1') -UIUser $uiUser -InstallDirectory $installRoot
      'Startup and recovery task registered.'
    }}
    @{ Name = 'Data Collector'; Install = {
      New-Item -ItemType Directory -Path $collectorData -Force | Out-Null
      $configPath = Join-Path $collectorData 'config.json'
      $credentialPath = Join-Path $collectorData 'collector-credential.dat'
      if (-not (Test-Path -LiteralPath $configPath)) {
        [IO.File]::WriteAllText($configPath, (New-CollectorConfiguration -Server $server -MachineCode $machine -WatchFolder $watchFolder), (New-Object Text.UTF8Encoding($false)))
      }
      if (-not (Test-Path -LiteralPath $credentialPath)) {
        if ($controls['Collector credential'].TextLength -lt 32) { throw 'The collector credential must be at least 32 characters.' }
        Invoke-WithSecureInput -FilePath (Join-Path $installRoot 'hospital-collector.exe') -Arguments "protect-credential `"$credentialPath`" `"$collectorUser`"" -Secret $controls['Collector credential'].SecurePassword
      }
      & (Join-Path $installRoot 'install-collector-task.ps1') -CollectorExecutable (Join-Path $installRoot 'hospital-collector.exe') -ConfigPath $configPath -ServiceUser $collectorUser
      'Configuration retained and independent startup task registered.'
    }}
  )

  $install.Enabled = $false
  $status.Clear()
  foreach ($component in $components) {
    try { $status.AppendText("$($component.Name): $(& $component.Install)`r`n") }
    catch { $status.AppendText("$($component.Name): Error - $($_.Exception.Message)`r`n") }
    [Windows.Forms.Application]::DoEvents()
  }
  $controls['Local TightVNC password'].Clear()
  $controls['Collector credential'].Clear()
  $install.Enabled = $true
})

[void]$form.ShowDialog()
