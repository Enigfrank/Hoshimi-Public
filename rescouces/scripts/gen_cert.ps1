#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$repoDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=prod-api-activity.ys4fun.com', $rsa, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$cert = $req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1), [DateTimeOffset]::UtcNow.AddDays(365))
$certBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$keyBytes = $rsa.ExportPkcs8PrivateKey()
[IO.File]::WriteAllText((Join-Path $repoDir 'cert.pem'), "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($certBytes, 1) + "`n-----END CERTIFICATE-----`n")
[IO.File]::WriteAllText((Join-Path $repoDir 'key.pem'), "-----BEGIN PRIVATE KEY-----`n" + [Convert]::ToBase64String($keyBytes, 1) + "`n-----END PRIVATE KEY-----`n")
Write-Output "证书和私钥已生成。"
