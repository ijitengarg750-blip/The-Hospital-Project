param(
    [string]$CollectorDirectory = "C:\HospitalCollector",
    [string]$ServiceUser = ""
)

$ErrorActionPreference = "Stop"
$Executable = Join-Path $CollectorDirectory "hospital-collector.exe"
$Config = Join-Path $CollectorDirectory "config.json"

if (-not (Test-Path $Executable)) { throw "Collector executable not found: $Executable" }
if (-not (Test-Path $Config)) { throw "Collector configuration not found: $Config" }
if ([string]::IsNullOrWhiteSpace($ServiceUser)) { $ServiceUser = Read-Host "Dedicated collector account (for example .\HospitalCollectorSvc)" }
$Credential = Get-Credential -UserName $ServiceUser -Message "Enter the dedicated collector account password"
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($PasswordPointer)
    $TaskCommand = '"' + $Executable + '" "' + $Config + '"'
    & schtasks.exe /Create /TN "Hospital Diagnostic Collector" /TR $TaskCommand /SC ONSTART /RU $ServiceUser /RP $PlainPassword /RL LIMITED /F
    if ($LASTEXITCODE -ne 0) { throw "schtasks.exe failed with exit code $LASTEXITCODE" }
    Write-Host "Collector startup task installed. Restart the PC and confirm collection still works."
}
finally {
    if ($PasswordPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer) }
    $PlainPassword = $null
}

