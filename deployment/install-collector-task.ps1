param(
    [string]$CollectorDirectory = "C:\HospitalCollector",
    [string]$CollectorExecutable = "",
    [string]$ConfigPath = "",
    [string]$ServiceUser = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($CollectorExecutable)) { $CollectorExecutable = Join-Path $CollectorDirectory "hospital-collector.exe" }
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $CollectorDirectory "config.json" }
$DataDirectory = Split-Path -Parent $ConfigPath

if (-not (Test-Path $CollectorExecutable)) { throw "Collector executable not found: $CollectorExecutable" }
if (-not (Test-Path $ConfigPath)) { throw "Collector configuration not found: $ConfigPath" }
$Configuration = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
if (-not $Configuration.collector_credential_file) { throw 'config.json must reference a DPAPI-protected collector_credential_file.' }
$CredentialFile = if ([IO.Path]::IsPathRooted($Configuration.collector_credential_file)) { $Configuration.collector_credential_file } else { Join-Path (Split-Path -Parent $ConfigPath) $Configuration.collector_credential_file }
if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) { throw "Protected collector credential not found: $CredentialFile" }
if ([string]::IsNullOrWhiteSpace($ServiceUser)) { $ServiceUser = Read-Host "Dedicated collector account (for example .\HospitalCollectorSvc)" }
$Credential = Get-Credential -UserName $ServiceUser -Message "Enter the dedicated collector account password"
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($PasswordPointer)
    $Scheduler = New-Object -ComObject 'Schedule.Service'
    $Scheduler.Connect()
    $Folder = $Scheduler.GetFolder('\')
    $Definition = $Scheduler.NewTask(0)
    $Definition.RegistrationInfo.Description = 'Uploads stable DICOM exports without modifying the source files.'
    $Definition.Principal.UserId = $ServiceUser
    $Definition.Principal.LogonType = 1
    $Definition.Principal.RunLevel = 0
    $Trigger = $Definition.Triggers.Create(8)
    $Trigger.Enabled = $true
    $Action = $Definition.Actions.Create(0)
    $Action.Path = $CollectorExecutable
    $Action.Arguments = '"' + $ConfigPath + '"'
    $Action.WorkingDirectory = Split-Path -Parent $CollectorExecutable
    $Definition.Settings.RestartCount = 5
    $Definition.Settings.RestartInterval = 'PT1M'
    $Definition.Settings.ExecutionTimeLimit = 'PT0S'
    $DataAcl = New-Object Security.AccessControl.DirectorySecurity
    $DataAcl.SetAccessRuleProtection($true, $false)
    $Inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $Propagation = [Security.AccessControl.PropagationFlags]::None
    $DataAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', $Inheritance, $Propagation, 'Allow')))
    $DataAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('Administrators', 'FullControl', $Inheritance, $Propagation, 'Allow')))
    $DataAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($ServiceUser, 'Modify', $Inheritance, $Propagation, 'Allow')))
    Set-Acl -LiteralPath $DataDirectory -AclObject $DataAcl
    $CredentialAcl = New-Object Security.AccessControl.FileSecurity
    $CredentialAcl.SetAccessRuleProtection($true, $false)
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')))
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('Administrators', 'FullControl', 'Allow')))
    $CredentialAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($ServiceUser, 'Read', 'Allow')))
    Set-Acl -LiteralPath $CredentialFile -AclObject $CredentialAcl
    $Folder.RegisterTaskDefinition('Hospital Diagnostic Collector', $Definition, 6, $ServiceUser, $PlainPassword, 1, $null) | Out-Null
    Write-Host "Collector startup task installed. Restart the PC and confirm collection still works."
}
finally {
    if ($PasswordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer) }
    $PlainPassword = $null
}

