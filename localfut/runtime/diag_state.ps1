$ErrorActionPreference = 'SilentlyContinue'

Write-Output '===== PORTS ====='
foreach ($p in 3216,42230,44321,8090,8080,8081,17502,42232,42231) {
  $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) {
    $pr = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    Write-Output ('{0,-6} LISTEN PID {1,-6} {2}' -f $p, $c.OwningProcess, $pr.ProcessName)
  } else {
    Write-Output ('{0,-6} FREE' -f $p)
  }
}

Write-Output ''
Write-Output '===== RELEVANT PROCESSES ====='
Get-Process | Where-Object { $_.ProcessName -match 'FIFA21|EADesktop|EABackground|Origin|EAApp|EALocalHostSvc' } |
  Select-Object Id,ProcessName,Path | Format-Table -AutoSize | Out-String -Width 200

Write-Output '===== FIFA21 REGISTRY KEYS ====='
$keys = 'HKLM:\SOFTWARE\EA Sports\FIFA 21','HKLM:\SOFTWARE\WOW6432Node\EA Sports\FIFA 21','HKCU:\SOFTWARE\EA Sports\FIFA 21','HKLM:\SOFTWARE\WOW6432Node\Electronic Arts','HKLM:\SOFTWARE\Electronic Arts','HKCU:\Software\Electronic Arts'
foreach ($k in $keys) {
  if (Test-Path $k) {
    Write-Output ('----- ' + $k + ' -----')
    Get-ItemProperty -Path $k -ErrorAction SilentlyContinue |
      Select-Object * -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider |
      Format-List | Out-String -Width 250
    Get-ChildItem -Path $k -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('  SUBKEY: ' + $_.PSChildName) }
  } else {
    Write-Output ('MISSING: ' + $k)
  }
}

Write-Output ''
Write-Output '===== HOSTS FILE (active entries) ====='
Get-Content -LiteralPath 'C:\Windows\System32\drivers\etc\hosts' -ErrorAction SilentlyContinue |
  Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }
