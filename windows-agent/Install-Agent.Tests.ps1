$installerPath = Join-Path $PSScriptRoot 'Install-Agent.ps1'
$installer = Get-Content -Raw -LiteralPath $installerPath

Describe 'Install-Agent signature preflight' {
  It 'validates its own signature before copying files' {
    $signatureCheck = $installer.IndexOf('$installerSignature = Get-AuthenticodeSignature')
    $firstCopy = $installer.IndexOf('Copy-Item $agentBinary')
    ($signatureCheck -ge 0 -and $signatureCheck -lt $firstCopy) | Should Be $true
  }

  It 'stops on failed preflight before copying or enrolling' {
    $preflightCheck = $installer.IndexOf('& $powerShell -NoProfile -ExecutionPolicy AllSigned -File $preflight')
    $preflightFailure = $installer.IndexOf("throw 'Windows installation preflight failed.'")
    $firstCopy = $installer.IndexOf('Copy-Item $agentBinary')
    $enrollment = $installer.IndexOf(' install --server $Server')
    ($preflightCheck -ge 0 -and $preflightCheck -lt $firstCopy -and $preflightCheck -lt $enrollment) | Should Be $true
    ($preflightFailure -gt $preflightCheck -and $preflightFailure -lt $firstCopy) | Should Be $true
  }

  It 'requires the agent and UI to have the same valid signer' {
    $installer.Contains('Assert-ValidPilotSignature -Path $agentBinary -ExpectedThumbprint $expectedThumbprint') | Should Be $true
    $installer.Contains('Assert-ValidPilotSignature -Path $uiScript -ExpectedThumbprint $expectedThumbprint') | Should Be $true
  }

  It 'uses verification-code approval without passing an enrollment secret through the browser' {
    $installer.Contains('The agent will show a verification code.') | Should Be $true
    $installer.Contains('one-time enrollment token') | Should Be $false
  }

  It 'uses a restartable per-user task instead of a startup launcher' {
    $notificationInstaller = Get-Content -Raw (Join-Path $PSScriptRoot 'Register-NotificationUI.ps1')
    $notificationInstaller.Contains("'Hospital Remote Notification UI'") | Should Be $true
    $notificationInstaller.Contains("RestartCount = 5") | Should Be $true
    $notificationInstaller.Contains("RestartInterval = 'PT1M'") | Should Be $true
    $installer.Contains("'HospitalRemoteUI.lnk'") | Should Be $false
    $installer.Contains("'HospitalRemoteUI.cmd'") | Should Be $false
  }

  It 'runs only the signed UI script under an interactive user token' {
    $notificationInstaller = Get-Content -Raw (Join-Path $PSScriptRoot 'Register-NotificationUI.ps1')
    $installer.Contains('-ExecutionPolicy AllSigned') | Should Be $true
    $notificationInstaller.Contains('-ExecutionPolicy AllSigned') | Should Be $true
    $notificationInstaller.Contains('LogonType = 3') | Should Be $true
  }

  It 'supports the MSI-installed layout and independent notification setup' {
    $installer.Contains('[switch]$InstalledLayout') | Should Be $true
    $installer.Contains('[switch]$SkipNotificationUI') | Should Be $true
    $installer.Contains('[Security.SecureString]$VNCPassword') | Should Be $true
    $installer.Contains("'Register-NotificationUI.ps1'") | Should Be $true
  }

  It 'configures connector service recovery independently' {
    $installer.Contains('sc.exe failure HospitalRemoteAgent reset= 86400 actions= restart/5000/restart/15000/restart/30000') | Should Be $true
  }
}

Describe 'Collector credential ACL' {
  It 'uses an exact protected ACL instead of retaining inherited or explicit grants' {
    $collectorInstaller = Get-Content -Raw (Join-Path $PSScriptRoot '..\deployment\install-collector-task.ps1')
    $collectorInstaller.Contains('SetAccessRuleProtection($true, $false)') | Should Be $true
    $collectorInstaller.Contains('Set-Acl -LiteralPath $CredentialFile') | Should Be $true
    $collectorInstaller.Contains('Set-Acl -LiteralPath $DataDirectory') | Should Be $true
    $collectorInstaller.Contains('icacls $CredentialFile') | Should Be $false
  }

  It 'supports MSI-managed executable and preserved data paths' {
    $collectorInstaller = Get-Content -Raw (Join-Path $PSScriptRoot '..\deployment\install-collector-task.ps1')
    $collectorInstaller.Contains('[string]$CollectorExecutable') | Should Be $true
    $collectorInstaller.Contains('[string]$ConfigPath') | Should Be $true
  }

  It 'does not expose the collector account password on a process command line' {
    $collectorInstaller = Get-Content -Raw (Join-Path $PSScriptRoot '..\deployment\install-collector-task.ps1')
    $collectorInstaller.Contains('schtasks.exe') | Should Be $false
    $collectorInstaller.Contains('RegisterTaskDefinition') | Should Be $true
  }
}

Describe 'New-PilotPackage defaults' {
  It 'calculates the default output directory after entering the script body' {
    $packageScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'New-PilotPackage.ps1')
    $parameterEnd = $packageScript.IndexOf("`n)`n")
    $defaultCalculation = $packageScript.IndexOf("if ([string]::IsNullOrWhiteSpace(`$OutputDirectory))")
    ($defaultCalculation -gt $parameterEnd) | Should Be $true
  }

  It 'packages and signs the reusable preflight component' {
    $packageScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'New-PilotPackage.ps1')
    $packageScript.Contains("'Preflight.ps1'") | Should Be $true
    $packageScript.Contains("(Join-Path `$OutputDirectory 'Preflight.ps1')") | Should Be $true
  }

  It 'packages and signs the notification task installer' {
    $packageScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'New-PilotPackage.ps1')
    $packageScript.Contains("'Register-NotificationUI.ps1'") | Should Be $true
    $packageScript.Contains("(Join-Path `$OutputDirectory 'Register-NotificationUI.ps1')") | Should Be $true
  }
}
