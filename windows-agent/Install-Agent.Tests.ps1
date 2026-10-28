$installerPath = Join-Path $PSScriptRoot 'Install-Agent.ps1'
$installer = Get-Content -Raw -LiteralPath $installerPath

Describe 'Install-Agent signature preflight' {
  It 'validates its own signature before copying files' {
    $signatureCheck = $installer.IndexOf('$installerSignature = Get-AuthenticodeSignature')
    $firstCopy = $installer.IndexOf('Copy-Item $agentBinary')
    ($signatureCheck -ge 0 -and $signatureCheck -lt $firstCopy) | Should Be $true
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
    $installer.Contains("'Hospital Remote Notification UI'") | Should Be $true
    $installer.Contains("RestartCount = 5") | Should Be $true
    $installer.Contains("RestartInterval = 'PT1M'") | Should Be $true
    $installer.Contains("'HospitalRemoteUI.lnk'") | Should Be $false
    $installer.Contains("'HospitalRemoteUI.cmd'") | Should Be $false
  }

  It 'runs only the signed UI script under an interactive user token' {
    $installer.Contains('-ExecutionPolicy AllSigned') | Should Be $true
    $installer.Contains('LogonType = 3') | Should Be $true
  }
}

Describe 'Collector credential ACL' {
  It 'uses an exact protected ACL instead of retaining inherited or explicit grants' {
    $collectorInstaller = Get-Content -Raw (Join-Path $PSScriptRoot '..\deployment\install-collector-task.ps1')
    $collectorInstaller.Contains('SetAccessRuleProtection($true, $false)') | Should Be $true
    $collectorInstaller.Contains('Set-Acl -LiteralPath $CredentialFile') | Should Be $true
    $collectorInstaller.Contains('icacls $CredentialFile') | Should Be $false
  }
}

Describe 'New-PilotPackage defaults' {
  It 'calculates the default output directory after entering the script body' {
    $packageScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'New-PilotPackage.ps1')
    $parameterEnd = $packageScript.IndexOf("`n)`n")
    $defaultCalculation = $packageScript.IndexOf("if ([string]::IsNullOrWhiteSpace(`$OutputDirectory))")
    ($defaultCalculation -gt $parameterEnd) | Should Be $true
  }
}
