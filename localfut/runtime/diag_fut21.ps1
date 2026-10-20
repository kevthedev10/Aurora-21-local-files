$ErrorActionPreference = 'SilentlyContinue'

Write-Output '===== GAME EXE ====='
$p = 'E:\fifa\FIFA 21\FIFA21.exe'
if (Test-Path -LiteralPath $p) {
  $f = Get-Item -LiteralPath $p
  $h = Get-FileHash -LiteralPath $p -Algorithm SHA256
  Write-Output ('EXISTS size=' + $f.Length)
  Write-Output ('SHA256=' + $h.Hash)
  Write-Output ('Version=' + $f.VersionInfo.FileVersion)
} else {
  Write-Output 'NOT FOUND at E:\fifa\FIFA 21\FIFA21.exe'
}

Write-Output ''
Write-Output '===== PROJECT PORTS ====='
foreach ($port in 3216,42230,44321,8090,8081,17502,42232,42231,8080) {
  $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) {
    $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    Write-Output ('{0,-6} LISTEN PID {1,-6} {2}' -f $port, $c.OwningProcess, $proc.ProcessName)
  } else {
    Write-Output ('{0,-6} FREE' -f $port)
  }
}

Write-Output ''
Write-Output '===== RELEVANT PROCESSES ====='
Get-Process | Where-Object { $_.ProcessName -match 'FIFA21|EADesktop|EABackground|Origin|EAApp|python|pythonw' } |
  Select-Object Id,ProcessName,Path | Format-Table -AutoSize | Out-String -Width 200

Write-Output '===== CRASH RECORDS (last 5 days) ====='
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddDays(-5)} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'FIFA21' -or $_.ProviderName -match 'Application Error|Windows Error Reporting' } |
  Select-Object -First 20 TimeCreated, Id, ProviderName |
  Format-Table -AutoSize | Out-String -Width 200
