$ErrorActionPreference = 'SilentlyContinue'

$keys = @(
  'HKLM:\SOFTWARE\Electronic Arts\EA Core',
  'HKLM:\SOFTWARE\WOW6432Node\Electronic Arts\EA Core',
  'HKLM:\SOFTWARE\Electronic Arts\EA Desktop',
  'HKLM:\SOFTWARE\WOW6432Node\Electronic Arts\EA Desktop',
  'HKCU:\Software\Electronic Arts\EA Core',
  'HKCU:\Software\Electronic Arts\EA Desktop',
  'HKCU:\Software\Electronic Arts\FIFA'
)
foreach ($k in $keys) {
  if (Test-Path $k) {
    Write-Output ('===== ' + $k + ' =====')
    Get-ItemProperty -Path $k -ErrorAction SilentlyContinue |
      Select-Object * -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider |
      Format-List | Out-String -Width 250
    Get-ChildItem -Path $k -ErrorAction SilentlyContinue | ForEach-Object {
      Write-Output ('  [SUBKEY] ' + $_.PSChildName)
      Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue |
        Select-Object * -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider |
        Format-List | Out-String -Width 250
    }
  } else {
    Write-Output ('MISSING: ' + $k)
  }
}

Write-Output ''
Write-Output '===== EACORE.INI / CONFIG SEARCH (FIFA dir) ====='
Get-ChildItem -Path 'E:\fifa\FIFA 21' -Recurse -Include '*.ini','eacore*','*.cfg','*.xml','*.json' -ErrorAction SilentlyContinue |
  Select-Object FullName,Length | Format-Table -AutoSize | Out-String -Width 250
