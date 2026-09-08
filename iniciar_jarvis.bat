@echo off
title Jarvis
cd /d "%~dp0"

set "HERMES_HOME=%USERPROFILE%\.hermes"
set "PATH=%HERMES_HOME%\bin;%PATH%"
set "HUD=%~dp0server"
set "PY=%HUD%\.venv\Scripts\python.exe"

echo Comprobando que no haya un Jarvis viejo corriendo desde otra carpeta...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*server.py*' -and $_.CommandLine -notlike '*Jarvis Apex*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Arrancando motor local (llama-server)...
powershell -NoProfile -Command "try { $t=New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',8081); $t.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 start "" /MIN wscript.exe "%HERMES_HOME%\llamacpp\Llama_Server.vbs"

echo Arrancando Hermes gateway...
powershell -NoProfile -Command "try { $t=New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',8642); $t.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 start "" /MIN hermes.exe gateway run --accept-hooks

echo Arrancando dashboard Hermes (9119)...
powershell -NoProfile -Command "try { $t=New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',9119); $t.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 start "" /MIN hermes.exe dashboard --no-open --skip-build --port 9119

echo Arrancando HUD...
powershell -NoProfile -Command "try { $t=New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',8766); $t.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 start "" /MIN /D "%HUD%" "%PY%" -u server.py

echo Esperando 8 segundos...
timeout /t 8 /nobreak >nul

start https://127.0.0.1:8766/hud/
echo Listo. Si el navegador avisa del certificado, pulsa Avanzado y continua.
pause
