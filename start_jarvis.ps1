# Arranca Ollama + Hermes gateway + dashboard 9119 + HUD.
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File C:\jarvis-hud-hermes\start_jarvis.ps1
$ErrorActionPreference = "Stop"
$env:HERMES_HOME = "$env:USERPROFILE\.hermes"
$env:Path = "$env:USERPROFILE\.hermes\bin;$env:Path"
$hudRoot = "C:\Jarvis Apex\jarvis-hud-hermes\server"
$py = "$hudRoot\.venv\Scripts\python.exe"

function Test-Port([int]$Port) {
  try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", $Port)
    $ok = $tcp.Connected
    $tcp.Close()
    return $ok
  } catch { return $false }
}

if (-not (Test-Port 11434)) {
  $ollama = Get-Command ollama -ErrorAction SilentlyContinue
  if ($ollama) {
    Start-Process -WindowStyle Hidden -FilePath $ollama.Source -ArgumentList "serve"
    Start-Sleep -Seconds 2
  }
}

if (-not (Test-Port 8642)) {
  Start-Process -WindowStyle Minimized -FilePath "hermes.exe" -ArgumentList "gateway","run","--accept-hooks"
  Start-Sleep -Seconds 4
}

if (-not (Test-Port 9119)) {
  Start-Process -WindowStyle Minimized -FilePath "hermes.exe" -ArgumentList "dashboard","--no-open","--skip-build","--port","9119"
}

if (-not (Test-Port 8790)) {
  $xttsRoot = "$hudRoot\xtts"
  Start-Process -WorkingDirectory $xttsRoot -FilePath "$xttsRoot\.venv\Scripts\python.exe" -ArgumentList "tts_service.py" -WindowStyle Minimized
}

if (-not (Test-Port 8766)) {
  Start-Process -WorkingDirectory $hudRoot -FilePath $py -ArgumentList "-u","server.py"
}

Write-Host "Ollama     http://127.0.0.1:11434"
Write-Host "Hermes API http://127.0.0.1:8642"
Write-Host "Dashboard  http://127.0.0.1:9119"
Write-Host "Voz (XTTS) http://127.0.0.1:8790 (carga el modelo, tarda ~10s en quedar listo)"
Write-Host "HUD        https://127.0.0.1:8766/hud/"
Write-Host "Parar:     powershell -NoProfile -File C:\jarvis-hud-hermes\parar_jarvis.ps1"
