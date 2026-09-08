Start-Process -FilePath "C:\Jarvis Apex\jarvis-hud-hermes\server\xtts\.venv\Scripts\python.exe" -ArgumentList "tts_service.py" -WorkingDirectory "C:\Jarvis Apex\jarvis-hud-hermes\server\xtts" -WindowStyle Minimized
Start-Process -FilePath "C:\Jarvis Apex\jarvis-hud-hermes\server\.venv\Scripts\python.exe" -ArgumentList "-u server.py" -WorkingDirectory "C:\Jarvis Apex\jarvis-hud-hermes\server" -WindowStyle Minimized
Write-Host "Services started."
