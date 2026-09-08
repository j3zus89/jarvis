# Para HUD, dashboard Hermes y (opcional) gateway.
$env:HERMES_HOME = "$env:USERPROFILE\.hermes"
$env:Path = "$env:USERPROFILE\.hermes\bin;$env:Path"

foreach ($port in 8765, 8766, 8790, 9119, 9443) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

# Gateway Hermes (8642). Ollama (11434) se deja; otras apps lo usan.
# Parada CORRECTA primero (hermes gateway stop) — matarlo con Stop-Process
# -Force le corta la sesion de golpe, sin dejarle guardar su estado interno
# (visto en sus logs: "exited UNCLEANLY (no exit path ran)"). Solo si sigue
# vivo tras esperar, se fuerza como ultimo recurso.
& hermes.exe gateway stop 2>$null
$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline) {
  $stillUp = Get-NetTCPConnection -LocalPort 8642 -State Listen -ErrorAction SilentlyContinue
  if (-not $stillUp) { break }
  Start-Sleep -Milliseconds 500
}
Get-NetTCPConnection -LocalPort 8642 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# Orphaned STT worker cleanup — docs/ARCHITECTURE.md gotcha #10: the voice
# recorder's multiprocessing child can survive a force-killed server.py
# (it doesn't own the listening socket, so the port-based kill above never
# touches it). Found live: 8 of these had piled up from earlier restarts
# tonight, ~600MB each, pushing system memory to 87%. Each one still
# advertises its parent's PID on its own command line, so a dead parent is
# how we tell an orphan from the one legitimate child of the server we just
# started fresh.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.CommandLine -match 'multiprocessing\.spawn.*parent_pid=(\d+)') {
    $parentId = [int]$Matches[1]
    if (-not (Get-Process -Id $parentId -ErrorAction SilentlyContinue)) {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}

Write-Host "Parado. Comprueba: Get-NetTCPConnection -LocalPort 8766,9119 -State Listen"
