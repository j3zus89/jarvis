# JARVIS_AUDIT_REPORT

## A. Rendimiento y Concurrencia (Event Loop & Latency)

### 1. Detección de Bloqueos en el Event Loop

- **`server/server.py`:** Se ha detectado una gran cantidad de bloqueos del Event Loop debido al uso de librerías y operaciones síncronas en endpoints asíncronos y rutinas de WebSocket.
  - El uso de la librería `requests` (e.g. `requests.post`, `requests.get`) es recurrente en todo el archivo (ej. `_call_fast_llm`, llamadas a Hermes, endpoints de telemetría como `machines`).
  - Hay llamadas a `subprocess.run` (ej. `_apply_voice_fx`, `_hermes_send_whatsapp`) que no se encuentran en un thread (con la excepción de algunas invocadas por asyncio.to_thread, pero otras quedan fuera).
  - La lectura de archivos locales se realiza de manera síncrona en múltiples partes (`Path(...).read_text()` en endpoints como `/api/activity` o en `selfcheck`), lo cual bloquea el Event Loop principal.
  - La escritura de archivos locales también es síncrona (`Path(...).write_text()` en lugares como `_save_history_locked` o `record_usage`).
  - La apertura de ventanas con `webbrowser.open` también se llama síncronamente.

- **`server/xtts/tts_service.py`:** El servicio local de clonación de voz se basa completamente en FastAPI con endpoints definidos como `def` (síncronos).
  - El punto `/tts` lee de disco síncronamente (`out_path.read_bytes()`).
  - FastAPI delega esto a su threadpool, pero si la concurrencia es alta, la inicialización del motor ML que carece de threads internos bloquea a otros workers.

### 2. Optimización del Pipeline de Streaming de Voz

- Se ha detectado la necesidad de optimización de Time-to-First-Byte (TTFB) en TTS. La segmentación de oraciones en `server.py` espera por `SENTENCE_RE` y podría retrasar el TTS local si se manejan fragmentos grandes o se bloquea el hilo principal esperando a APIs o discos.

### B. Resiliencia, Logs y Gestión de Memoria

#### 1. Fugas de Memoria (Memory Leaks) en Frontend 3D

- **HUD Holográfico (`server/hud/index.html`):** Los paneles emergentes (que usan CSS o animaciones) se destruyen en el DOM vía `p.remove()` después de las animaciones `holoDismiss`. Sin embargo, cualquier listener subyacente que pudiese quedarse pendiente, y las mallas o texturas instanciadas, no parecen recibir rutinas explícitas de limpieza.
- **Holograma 3D WebGL/Three.js (`server/hud/humanoid3d/app.js`):** La instanciación de materiales, geometrías (`THREE.BufferGeometry`), texturas y partículas en el canvas nunca se limpian utilizando el comando `.dispose()`. Si los elementos se recargan, se ocultan, o si se cierran los paneles 3D que referencian el motor, ocurre un memory leak pasivo a lo largo de las sesiones largas típicas del "dashboard".

#### 2. Prevención de Explosión de Logs y Failover Automático
- La telemetría en `/api/activity` hace `_ACTIVITY_LOG_PATH.read_text()` directamente, lo que crecerá de forma indefinida y causará lag O(N) si no hay rotación o límites (`RotatingFileHandler` no está implementado).

## C. Arquitectura y Calidad de Código (Refactor Modular)

- `server.py` es actualmente un archivo monolítico enorme (casi 4,000 líneas de código).
- La lógica del manejo de LLM/Groq, STT/Whisper, la integración con la API proxy de Hermes, Websockets, telemetría de disco, y manipulación de audio FFmpeg se mezclan en el mismo archivo sin una clara división por enrutadores de FastAPI (`routers`).
- Falta de modelos Pydantic estrictos para parsear los payloads que entran por el WS, todo es casteado desde diccionarios.

## D. Seguridad y Sanitización

- Se observan filtros (`SECRET_RES` en `_clean_for_tts`) que son una buena base, pero la falta de separación del middleware CORS (`ALLOWED_ORIGIN_HOSTS`) podría optimizarse. El hardcodeo de URIs y configuraciones debería extraerse si es necesario.