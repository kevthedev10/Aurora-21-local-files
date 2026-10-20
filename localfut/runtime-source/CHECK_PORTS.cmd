@echo off
setlocal
echo FIFA 21 Local FUT - port diagnostics
echo ====================================
echo.
for %%P in (42230 44321 10051 8081 17502 42232 42231 3216) do (
  echo ---- PORT %%P ----
  netstat -ano -p tcp | findstr ":%%P "
  if errorlevel 1 echo free / no TCP listener shown
  echo.
)
echo If a line is shown, the last number is the PID.
echo You can identify it with: tasklist /FI "PID eq NUMBER"
echo.
pause
