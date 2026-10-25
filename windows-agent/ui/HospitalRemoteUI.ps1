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
