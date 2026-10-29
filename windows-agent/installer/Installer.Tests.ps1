$installerRoot = $PSScriptRoot
$productPath = Join-Path $installerRoot 'Product.wxs'
$buildPath = Join-Path $installerRoot 'Build-Installer.ps1'
$configurePath = Join-Path $installerRoot 'Configure-Installation.ps1'
$removePath = Join-Path $installerRoot 'Remove-Installation.ps1'

Describe 'Phase 3C MSI source' {
  It 'contains valid WiX source with rollback-safe major upgrades' {
    Test-Path -LiteralPath $productPath | Should Be $true
    [xml]$product = Get-Content -Raw -LiteralPath $productPath
    $source = $product.OuterXml
    $source.Contains('Scope="perMachine"') | Should Be $true
    $source.Contains('Schedule="afterInstallInitialize"') | Should Be $true
    $source.Contains('AllowDowngrades="yes"') | Should Be $false
  }

  It 'keeps connector collector and notification UI independently repairable' {
    [xml]$product = Get-Content -Raw -LiteralPath $productPath
    $source = $product.OuterXml
    $source.Contains('Id="ConnectorFeature"') | Should Be $true
    $source.Contains('Id="CollectorFeature"') | Should Be $true
    $source.Contains('Id="NotificationFeature"') | Should Be $true
  }

  It 'does not package or reconfigure TightVNC' {
    $source = Get-Content -Raw -LiteralPath $productPath
    $source.Contains('TightVNC') | Should Be $false
    $source.Contains('5900') | Should Be $false
  }

  It 'retains enrollment and collector data unless removal is explicit' {
    $source = Get-Content -Raw -LiteralPath $productPath
    $source.Contains('REMOVE_CONFIGURATION') | Should Be $true
    $source.Contains('NOT UPGRADINGPRODUCTCODE') | Should Be $true
    $source.Contains('repair-service-if-configured') | Should Be $true
    $source.Contains('RemoveFolder Id="RemoveConnectorData"') | Should Be $false
    $source.Contains('RemoveFolder Id="RemoveCollectorData"') | Should Be $false
  }

  It 'stops the connector around file replacement and repairs it after install' {
    $source = Get-Content -Raw -LiteralPath $productPath
    $source.Contains('ServiceControl') | Should Be $true
    $source.Contains('Remove="uninstall"') | Should Be $true
    $source.Contains('After="InstallFiles"') | Should Be $true
    $source.Contains('Id="RepairConfiguredConnector"') | Should Be $true
  }

  It 'has a pinned fail-closed build workflow' {
    $source = Get-Content -Raw -LiteralPath $buildPath
    $source.Contains("'4.0.6'") | Should Be $true
    $source.Contains('go.exe test ./...') | Should Be $true
    $source.Contains('go.exe build -trimpath') | Should Be $true
    $source.Contains("'hospital-remote-agent.exe'") | Should Be $true
    $source.Contains("'hospital-installer-launcher.exe'") | Should Be $true
    $source.Contains("'hospital-collector.exe'") | Should Be $true
    $source.Contains('wix.exe') | Should Be $true
  }
}

Describe 'Phase 3C graphical configuration' {
  BeforeAll {
    . $configurePath -NoRun
  }

  It 'writes collector configuration without credentials' {
    $json = New-CollectorConfiguration -Server 'https://dashboard.example' -MachineCode 'ROOM-1' -WatchFolder 'C:\PACS_EXPORT'
    $config = $json | ConvertFrom-Json
    $config.machine_code | Should Be 'ROOM-1'
    $config.server_url | Should Be 'https://dashboard.example'
    $config.watch_folders[0] | Should Be 'C:\PACS_EXPORT'
    $json.Contains('collector_key') | Should Be $false
    $json.Contains('password') | Should Be $false
  }

  It 'writes collector JSON as UTF-8 without a Windows PowerShell BOM' {
    $source = Get-Content -Raw -LiteralPath $configurePath
    $source.Contains('Text.UTF8Encoding($false)') | Should Be $true
    $source.Contains('Set-Content -LiteralPath $configPath -Encoding UTF8') | Should Be $false
  }

  It 'rejects insecure or credential-bearing server URLs' {
    (Test-DashboardServer 'https://dashboard.example') | Should Be $true
    (Test-DashboardServer 'http://dashboard.example') | Should Be $false
    (Test-DashboardServer 'https://user:secret@dashboard.example') | Should Be $false
    (Test-DashboardServer 'https://dashboard.example/?token=secret') | Should Be $false
    (Test-DashboardServer 'https://dashboard.example/?value=unexpected') | Should Be $false
  }

  It 'uses masked controls for both local credentials' {
    $source = Get-Content -Raw -LiteralPath $configurePath
    $source.Contains("@('Local TightVNC password', 'Collector credential')") | Should Be $true
    $source.Contains('UseSystemPasswordChar = $true') | Should Be $true
  }

  It 'turns the Start-menu configuration shortcut into an elevated graphical flow' {
    $source = Get-Content -Raw -LiteralPath $configurePath
    $source.Contains("-Verb RunAs") | Should Be $true
    $source.Contains("-ExecutionPolicy', 'AllSigned'") | Should Be $true
  }

  It 'uses advertised Start-menu shortcuts owned by their launcher component' {
    [xml]$product = Get-Content -Raw -LiteralPath $productPath
    $source = $product.OuterXml
    $source.Contains('Id="InstallerLauncherExe"') | Should Be $true
    $source.Contains('Arguments="configure"') | Should Be $true
    $source.Contains('Arguments="uninstall [ProductCode]"') | Should Be $true
    ([regex]::Matches($source, 'Advertise="yes"').Count) | Should Be 2
    $source.Contains('Id="MenuShortcuts"') | Should Be $false
  }

  It 'keeps component failures independent' {
    $source = Get-Content -Raw -LiteralPath $configurePath
    $source.Contains("'Remote Connector'") | Should Be $true
    $source.Contains("'Data Collector'") | Should Be $true
    $source.Contains('foreach ($component in $components)') | Should Be $true
  }

  It 'removes optional component tasks when their MSI features are removed' {
    $product = Get-Content -Raw -LiteralPath $productPath
    $remove = Get-Content -Raw -LiteralPath $removePath
    $product.Contains('RemoveCollectorTask') | Should Be $true
    $product.Contains('RemoveNotificationTask') | Should Be $true
    $remove.Contains("[ValidateSet('All', 'Collector', 'Notification')]") | Should Be $true
  }
}

Describe 'Phase 3C uninstall behavior' {
  It 'retains data by default and supports explicit removal' {
    $source = Get-Content -Raw -LiteralPath $removePath
    $source.Contains("[ValidateSet('0', '1')]") | Should Be $true
    $source.Contains("[string]`$RemoveConfiguration = '0'") | Should Be $true
    $source.Contains("if (`$RemoveConfiguration -eq '1')") | Should Be $true
    $source.Contains('-Recurse') | Should Be $false
  }

  It 'preserves enrollment credentials and queued report state by default' {
    $source = Get-Content -Raw -LiteralPath $removePath
    $source.Contains("[string]`$RemoveConfiguration = '0'") | Should Be $true
    $source.Contains("if (`$RemoveConfiguration -eq '1')") | Should Be $true
    $source.Contains("'service.dat'") | Should Be $true
    $source.Contains("'collector-credential.dat'") | Should Be $true
    $source.Contains("'state.json'") | Should Be $true
    $source.Contains('.dcm') | Should Be $false
  }
}
