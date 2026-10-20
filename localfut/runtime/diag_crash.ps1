$ErrorActionPreference = 'SilentlyContinue'
Write-Output '===== WER 1001 DETAILS (FIFA21) ====='
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddDays(-2)} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'FIFA21' } |
  Select-Object -First 6 TimeCreated, Id |
  ForEach-Object {
    Write-Output ('--- ' + $_.TimeCreated + ' ---')
    Write-Output $_.Message
    Write-Output ''
  }

Write-Output '===== WER REPORT FILES ====='
$wer = "$env:ProgramData\Microsoft\Windows\WER\ReportArchive"
if (Test-Path $wer) {
  Get-ChildItem -Path $wer -Recurse -Filter '*.wer' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'FIFA21' -or ($_.LastWriteTime -gt (Get-Date).AddDays(-2)) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 12 FullName, LastWriteTime |
    Format-List | Out-String -Width 200
}
$crash = "$env:LOCALAPPDATA\CrashDumps"
if (Test-Path $crash) {
  Write-Output '===== CRASHDUMPS ====='
  Get-ChildItem -Path $crash -Filter 'FIFA21*' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 10 FullName, Length, LastWriteTime |
    Format-List | Out-String -Width 200
}
