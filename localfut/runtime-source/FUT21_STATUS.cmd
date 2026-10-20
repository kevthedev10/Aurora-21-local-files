@echo off
echo === FIFA 21 Local FUT ports ===
for %%P in (42230 42231 44321 8090 8081 17502 42232 3216) do (
  netstat -ano | findstr ":%%P " | findstr LISTENING
)
pause
