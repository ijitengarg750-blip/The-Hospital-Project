param(
    [string]$CollectorDirectory = "C:\HospitalCollector",
    [string]$ServiceUser = ""
)

$ErrorActionPreference = "Stop"
$Executable = Join-Path $CollectorDirectory "hospital-collector.exe"
$Config = Join-Path $CollectorDirectory "config.json"

if (-not (Test-Path $Executable)) { throw "Collector executable not found: $Executable" }
if (-not (Test-Path $Config)) { throw "Collector configuration not found: $Config" }
$Configuration = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
if (-not $Configuration.collector_credential_file) { throw 'config.json must reference a DPAPI-protected collector_credential_file.' }
$CredentialFile = if ([IO.Path]::IsPathRooted($Configuration.collector_credential_file)) { $Configuration.collector_credential_file } else { Join-Path $CollectorDirectory $Configuration.collector_credential_file }
if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) { throw "Protected collector credential not found: $CredentialFile" }
if ([string]::IsNullOrWhiteSpace($ServiceUser)) { $ServiceUser = Read-Host "Dedicated collector account (for example .\HospitalCollectorSvc)" }
$Credential = Get-Credential -UserName $ServiceUser -Message "Enter the dedicated collector account password"
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($PasswordPointer)
    $TaskCommand = '"' + $Executable + '" "' + $Config + '"'
    & schtasks.exe /Create /TN "Hospital Diagnostic Collector" /TR $TaskCommand /SC ONSTART /RU $ServiceUser /RP $PlainPassword /RL LIMITED /F
    if ($LASTEXITCODE -ne 0) { throw "schtasks.exe failed with exit code $LASTEXITCODE" }

    $Scheduler = New-Object -ComObject 'Schedule.Service'
    $Scheduler.Connect()
    $Folder = $Scheduler.GetFolder('\')
    $Task = $Folder.GetTask('Hospital Diagnostic Collector')
    $Definition = $Task.Definition
    $Definition.Settings.RestartCount = 5
    $Definition.Settings.RestartInterval = 'PT1M'
    $Folder.RegisterTaskDefinition('Hospital Diagnostic Collector', $Definition, 6, $ServiceUser, $PlainPassword, 1, $null) | Out-Null
    $CredentialAcl = New-Object Security.AccessControl.FileSecurity
    $CredentialAcl.SetAccessRuleProtection($true, $false)
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')))
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('Administrators', 'FullControl', 'Allow')))
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($ServiceUser, 'Read', 'Allow')))
    Set-Acl -LiteralPath $CredentialFile -AclObject $CredentialAcl
    Write-Host "Collector startup task installed. Restart the PC and confirm collection still works."
}
finally {
    if ($PasswordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer) }
    $PlainPassword = $null
}

