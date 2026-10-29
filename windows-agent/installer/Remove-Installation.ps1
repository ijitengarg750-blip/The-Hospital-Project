param(
  [ValidateSet('0', '1')][string]$RemoveConfiguration = '0',
  [ValidateSet('All', 'Collector', 'Notification')][string]$Component = 'All',
  [string]$ProductCode,
  [switch]$Prompt
)

$ErrorActionPreference = 'Stop'

if ($Prompt) {
  Add-Type -AssemblyName System.Windows.Forms
  $choice = [Windows.Forms.MessageBox]::Show(
    "Choose Yes to retain enrollment, collector configuration, and queued-report state. Choose No to remove those local records. Source DICOM files are never deleted.",
    'Uninstall Hospital Diagnostic Components',
    [Windows.Forms.MessageBoxButtons]::YesNoCancel,
    [Windows.Forms.MessageBoxIcon]::Warning
  )
  if ($choice -eq [Windows.Forms.DialogResult]::Cancel) { exit 0 }
  $remove = if ($choice -eq [Windows.Forms.DialogResult]::No) { '1' } else { '0' }
  if ($ProductCode -notmatch '^\{[0-9A-Fa-f-]{36}\}$') { throw 'The MSI product code is invalid.' }
  $process = Start-Process msiexec.exe -ArgumentList @('/x', $ProductCode, "REMOVE_CONFIGURATION=$remove") -Verb RunAs -Wait -PassThru
  exit $process.ExitCode
}

$taskNames = switch ($Component) {
  'Collector' { @('Hospital Diagnostic Collector') }
  'Notification' { @('Hospital Remote Notification UI') }
  default { @('Hospital Remote Notification UI', 'Hospital Diagnostic Collector') }
}
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$taskFolder = $taskService.GetFolder('\')
foreach ($taskName in $taskNames) {
  try { $taskFolder.DeleteTask($taskName, 0) } catch [Runtime.InteropServices.COMException] {
    if ($_.Exception.HResult -ne -2147024894) { throw }
  }
}
if ($Component -ne 'All') { exit 0 }

$agent = Join-Path $env:ProgramFiles 'Hospital Diagnostic Components\hospital-remote-agent.exe'
if ((Get-Service -Name 'HospitalRemoteAgent' -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $agent -PathType Leaf)) {
  & $agent uninstall
  if ($LASTEXITCODE -ne 0) { throw 'Remote Connector service removal failed.' }
}

if ($RemoveConfiguration -eq '1') {
  $connectorData = Join-Path $env:ProgramData 'HospitalRemoteAgent'
  $collectorData = Join-Path $env:ProgramData 'HospitalDataCollector'
  foreach ($path in @(
    (Join-Path $connectorData 'service.dat'),
    (Join-Path $connectorData 'service.dat.pending'),
    (Join-Path $connectorData 'ui.dat'),
    (Join-Path $collectorData 'config.json'),
    (Join-Path $collectorData 'collector-credential.dat'),
    (Join-Path $collectorData 'state.json'),
    (Join-Path $collectorData 'health.json')
  )) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
  }
  Remove-Item -LiteralPath $connectorData, $collectorData -Force -ErrorAction SilentlyContinue
}
