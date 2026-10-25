param(
  [Parameter(Mandatory=$true)][string]$ServerIP,
  [string]$ServerName = 'hospital-dashboard.test'
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tls = Join-Path $repository 'deployment\test-tls'
New-Item -ItemType Directory -Path $tls -Force | Out-Null
@"
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$ServerIP,DNS:$ServerName
"@ | Set-Content -Encoding ASCII (Join-Path $tls 'server.ext')

Push-Location $repository
try {
  docker compose run --rm --no-deps -v "${tls}:/certs" --entrypoint openssl backend req -x509 -newkey rsa:3072 -nodes -sha256 -days 30 -keyout /certs/ca.key -out /certs/ca.crt -subj '/CN=Hospital Dashboard Test CA' -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign'
  if ($LASTEXITCODE -ne 0) { throw 'Unable to create the test CA.' }
  docker compose run --rm --no-deps -v "${tls}:/certs" --entrypoint openssl backend req -newkey rsa:3072 -nodes -sha256 -keyout /certs/server.key -out /certs/server.csr -subj "/CN=$ServerName"
  if ($LASTEXITCODE -ne 0) { throw 'Unable to create the test server request.' }
  docker compose run --rm --no-deps -v "${tls}:/certs" --entrypoint openssl backend x509 -req -sha256 -days 30 -in /certs/server.csr -CA /certs/ca.crt -CAkey /certs/ca.key -CAcreateserial -out /certs/server.crt -extfile /certs/server.ext
  if ($LASTEXITCODE -ne 0) { throw 'Unable to sign the test server certificate.' }
} finally { Pop-Location }

icacls (Join-Path $tls 'ca.key') /inheritance:r /grant:r "$env:USERNAME`:F" | Out-Null
icacls (Join-Path $tls 'server.key') /inheritance:r /grant:r "$env:USERNAME`:F" | Out-Null
Write-Host "Created a 30-day test CA and TLS certificate for $ServerIP. Import deployment\test-tls\ca.crt only on the two test laptops."
