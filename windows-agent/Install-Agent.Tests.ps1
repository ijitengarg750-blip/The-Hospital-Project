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

  It 'calls the enrollment secret a token' {
    $installer.Contains('Paste the one-time enrollment token when prompted.') | Should Be $true
  }

  It 'uses a shortcut instead of generating an unsigned command script' {
    $installer.Contains("'HospitalRemoteUI.lnk'") | Should Be $true
    $installer.Contains("'HospitalRemoteUI.cmd'") | Should Be $false
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
