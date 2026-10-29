Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Security
Add-Type -AssemblyName System.Web.Extensions

$configPath = Join-Path $env:ProgramData 'HospitalRemoteAgent\ui.dat'
$protected = [IO.File]::ReadAllBytes($configPath)
$plain = [Security.Cryptography.ProtectedData]::Unprotect($protected, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
$serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
try { $config = $serializer.DeserializeObject([Text.Encoding]::UTF8.GetString($plain)) }
finally { [Array]::Clear($plain, 0, $plain.Length) }
$authorization = "Bearer $($config.ui_token)"

function Invoke-LocalAgent {
  param([string]$Method, [string]$Path, [object]$Body = $null)
  $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:19377$Path")
  $request.Method = $Method
  $request.Timeout = 2000
  $request.Headers.Add('Authorization', $authorization)
  if ($Body -ne $null) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($serializer.Serialize($Body))
    $request.ContentType = 'application/json'
    $request.ContentLength = $bytes.Length
    $stream = $request.GetRequestStream()
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Close() }
  }
  $response = $request.GetResponse()
  try {
    $reader = New-Object IO.StreamReader $response.GetResponseStream()
    try { $text = $reader.ReadToEnd() } finally { $reader.Close() }
    if ($text) { return $serializer.DeserializeObject($text) }
  } finally { $response.Close() }
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="Hospital remote session" Height="430" Width="440" WindowStartupLocation="CenterScreen" ShowInTaskbar="True" Topmost="True" Visibility="Hidden">
  <Grid Margin="18"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
    <TextBlock Name="TitleText" FontSize="20" FontWeight="SemiBold" Text="Remote diagnostic session active"/>
    <TextBlock Name="DetailText" Grid.Row="1" Margin="0,8,0,12" TextWrapping="Wrap"/>
    <ListBox Name="Messages" Grid.Row="2" Margin="0,0,0,12"/>
    <TextBox Name="MessageText" Grid.Row="3" Height="64" MaxLength="500" AcceptsReturn="True" TextWrapping="Wrap"/>
    <StackPanel Grid.Row="4" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0"><Button Name="SendButton" Content="Send message" Padding="14,7" Margin="0,0,8,0"/><Button Name="EndButton" Content="End session" Padding="14,7" Background="#A52B2B" Foreground="White"/></StackPanel>
  </Grid>
</Window>
'@
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$detail = $window.FindName('DetailText'); $messages = $window.FindName('Messages'); $messageText = $window.FindName('MessageText')
$send = $window.FindName('SendButton'); $end = $window.FindName('EndButton')
$lastSession = $null; $lastMessage = 0

$send.Add_Click({
  $body = $messageText.Text.Trim()
  if ($body) { Invoke-LocalAgent 'POST' '/messages' @{body=$body}; $messageText.Clear() }
})
$end.Add_Click({ Invoke-LocalAgent 'POST' '/end'; $window.Hide() })
$window.Add_Closing({ $_.Cancel = $true; $window.WindowState = 'Minimized' })

$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(2)
$timer.Add_Tick({
  try {
    $state = Invoke-LocalAgent 'GET' '/state'
    if ($state.session) {
      if ($lastSession -ne $state.session.id) { $messages.Items.Clear(); $lastMessage = 0; $lastSession = $state.session.id; $window.Show(); $window.Activate() }
      $detail.Text = "Doctor: $($state.session.doctor_username)  |  PC state: $($state.machine_state)"
      foreach ($message in $state.messages) { if ($message.id -gt $lastMessage) { [void]$messages.Items.Add("$($message.sender): $($message.body)"); $lastMessage = $message.id } }
    } elseif ($lastSession) { $lastSession = $null; $window.Hide() }
  } catch { }
})
$timer.Start()
[void]$window.ShowDialog()

# SIG # Begin signature block
# MIIHTgYJKoZIhvcNAQcCoIIHPzCCBzsCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCBxn3i5+uOCrDtM
# /kczDXYoB9xTA+MBouPt3oXptZ4KdKCCBDAwggQsMIIClKADAgECAhA33nkWJxw9
# pUTEsQCq7EsBMA0GCSqGSIb3DQEBCwUAMC4xLDAqBgNVBAMMI0hvc3BpdGFsIERp
# YWdub3N0aWMgRGFzaGJvYXJkIFBpbG90MB4XDTI2MTAyNzExNTQ0MVoXDTI4MTAy
# NzEyMDQ0MlowLjEsMCoGA1UEAwwjSG9zcGl0YWwgRGlhZ25vc3RpYyBEYXNoYm9h
# cmQgUGlsb3QwggGiMA0GCSqGSIb3DQEBAQUAA4IBjwAwggGKAoIBgQCVtwLIHQhm
# FR8ah1ZrDRRgt9ods2OaAlpXR0UdfTLxJv/ghJGQT9xE+VGFM/mF+RZiACn0eXF5
# ozg44ygDktpilsVgageWwa7N2AjPNvtZUIWbfmiD2GoIDC5V5FLrSMedB2TMnVt2
# LKN4a0zqletP+oE16VZLCPB2t8FE+yVotWrDMwaPuDkwm+OK2WkpBZRV5vet036r
# elf1sEobUrO3+M8v11COcpnoq8KKP4HQ4iqmP1wz+YkryiJlfn6LoF3KVFJFsm2Q
# j9qXuH4NRwKRTKtPPfrrKDIecibCyzCVLzM1yrO0+jgeX4mWSYm+zZu0gOGNi4rM
# XqaO6z6HebjnAtZTXNShwH/PQOY70UWj5dpvjiHpbNBP5HwkqW95n/X2/q+rDtoO
# B8wMe9ld/wg6CEvTxqBPe6RcOOuXOaTORoM4lP0ZR+mbTp2RA0XapXAgu8XVsvOt
# uI4+EMUAnmqucso+iEPMBGVh0TvwX1ytMJO1ZzivQbV9dHapKMmfYrUCAwEAAaNG
# MEQwDgYDVR0PAQH/BAQDAgeAMBMGA1UdJQQMMAoGCCsGAQUFBwMDMB0GA1UdDgQW
# BBQTyU45KVffaqkfDCByzUnLxcJT1zANBgkqhkiG9w0BAQsFAAOCAYEAZXUAlIsQ
# xYhF52VyGLAQD2UtZjqA+N4z6UfgsMQZa2DTAcN08aN9t+PAXN0udeMaGdeIlAdw
# DOb4OssBT2dTsNNsiZQkcTUiVz+NmLaAWGX9sPhY3xqf6heJL/Zc9otNU74c2I/w
# ogr8qizI4HdtHpUxn6ILaLO7qkB/AQe6AuHS6NFMdPADE5EvtECLmvsHnMjpvEz+
# rFM1kH1kloi926zdfEkUaVgKk55ttXhYrQkhpoudGyEy1R0q3hil5MbsaC82i2/g
# P1ug24XknZNi4C/CzcPISR/VGQnZUYznyal1pWFxOW4BBnOxd1uYIxfOhZ5xfgne
# pIJVbjtBa9++pXb2qWAHvittRcpNc5ISero3njzwN07ZiMFagGFYuhrwCjMWksib
# uHbCaAJLM+zaSqIGYrHimaTWQnqYsKJwFNYbpeG6U6C1huc2pki2H8ODEQ7kxqPk
# mPuc926c6a4wRQumwslEq3m9RhhDaqIw1MSt8/c7I1/qScn0OePNrQLpMYICdDCC
# AnACAQEwQjAuMSwwKgYDVQQDDCNIb3NwaXRhbCBEaWFnbm9zdGljIERhc2hib2Fy
# ZCBQaWxvdAIQN955FiccPaVExLEAquxLATANBglghkgBZQMEAgEFAKCBhDAYBgor
# BgEEAYI3AgEMMQowCKACgAChAoAAMBkGCSqGSIb3DQEJAzEMBgorBgEEAYI3AgEE
# MBwGCisGAQQBgjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCB1
# 6R7BW0sOyGlPBmEquKxLwhHJjMN6aiTW5Vo9fOMtkzANBgkqhkiG9w0BAQEFAASC
# AYCSR44T39bDCSb4qQ6GeYUy6xJr3V9GnLKVJPPCcLcusaUagmYi6BHEtVP3ekS/
# 4X9xMLqsat62H/Yx9dbW1LmJzU4OcuZ90rRBs5NkNeeiWLP0V7eRFemGmB3rAX4J
# 88Hea3jCV5AK83a5MuPpxWKeCIEckKPkf38aooxRX/Xk61NDBo78KpiNC3zN8j77
# 4QotlIuIN9GupTFEHACggmO+F2dNyMmFvHtKIhorTm0b9FEAgbBOooUMPkpYpprh
# Bi9HQ/jVoi730Mx4QQ/tY3b7Hg0gJJ9G+d7WsEsiPpUlpsVBb168yqYVy6dz6h5f
# 4TjDc3pNq4G4M6hQPv1oVQrnf0wxtB+jGHZQ66VZurDuTl5qN/ouYwADmx2f+81/
# c+6lz4fuBImHUcWX0ZQATxSs0wqHNFJxXOrS/rjV2mVDMp38u98r3f8JKe+cRrPi
# ZFlQgk9sH6BIK807vPgHJU5MPoTiCQ8AsPI/5oBw5wbH+CNNCMvfDe0tWxvpIahf
# U34=
# SIG # End signature block
