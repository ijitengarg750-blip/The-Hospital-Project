$preflightPath = Join-Path $PSScriptRoot 'Preflight.ps1'
. $preflightPath

Describe 'Windows installation preflight contract' {
  It 'uses only the four documented result states' {
    (New-PreflightResult -Name 'test' -Status 'Passed' -Message 'ok').Status | Should Be 'Passed'
    (New-PreflightResult -Name 'test' -Status 'Warning' -Message 'check').Status | Should Be 'Warning'
    (New-PreflightResult -Name 'test' -Status 'Failed' -Message 'stop').Status | Should Be 'Failed'
    (New-PreflightResult -Name 'test' -Status 'Not Tested' -Message 'unknown').Status | Should Be 'Not Tested'
    { New-PreflightResult -Name 'test' -Status 'Unknown' -Message 'bad' } | Should Throw
  }

  It 'fails unsupported Windows or CPU combinations' {
    (Get-PlatformResult -Version ([Version]'10.0') -Architecture 'AMD64').Status | Should Be 'Passed'
    (Get-PlatformResult -Version ([Version]'6.3') -Architecture 'AMD64').Status | Should Be 'Failed'
    (Get-PlatformResult -Version ([Version]'10.0') -Architecture 'ARM64').Status | Should Be 'Failed'
    (Get-PlatformResult -Version ([Version]'11.0') -Architecture 'AMD64').Status | Should Be 'Failed'
  }

  It 'does not execute an agent whose package signature failed' {
    (Get-AppControlResult -AgentPath 'missing.exe' -SignatureValid $false).Status | Should Be 'Not Tested'
  }

  It 'requires TightVNC to listen only on loopback' {
    (Get-LoopbackResult -Addresses @('127.0.0.1', '::1')).Status | Should Be 'Passed'
    (Get-LoopbackResult -Addresses @('0.0.0.0')).Status | Should Be 'Failed'
    (Get-LoopbackResult -Addresses @()).Status | Should Be 'Failed'
  }

  It 'distinguishes clean, existing, and partial installations' {
    (Get-InstallStateResult -ServiceExists $false -BinaryExists $false -ConfigExists $false).Status | Should Be 'Passed'
    (Get-InstallStateResult -ServiceExists $true -BinaryExists $true -ConfigExists $true).Status | Should Be 'Warning'
    (Get-InstallStateResult -ServiceExists $true -BinaryExists $false -ConfigExists $false).Status | Should Be 'Failed'
  }

  It 'returns failure only when at least one check failed' {
    (Get-PreflightExitCode @(
      (New-PreflightResult -Name 'one' -Status 'Passed' -Message 'ok'),
      (New-PreflightResult -Name 'two' -Status 'Warning' -Message 'check')
    )) | Should Be 0
    (Get-PreflightExitCode @(
      (New-PreflightResult -Name 'one' -Status 'Failed' -Message 'stop')
    )) | Should Be 1
  }

  It 'returns flat named results when the server URL is unsafe' {
    $results = @(Get-NetworkResults 'http://invalid.example')
    $results.Count | Should Be 2
    $results[0].Name | Should Be 'DNS resolution'
    $results[1].Name | Should Be 'HTTPS/WSS TLS on port 443'
    $results[1].Status | Should Be 'Failed'
  }

  It 'contains no installation, enrollment, removal, or policy-changing action' {
    $source = Get-Content -Raw -LiteralPath $preflightPath
    $source | Should Not Match 'EnrollWithApproval|\binstall\s+--|New-Service|Remove-Item|Set-Acl|Set-AppLockerPolicy|Copy-Item|New-NetFirewallRule'
  }
}
