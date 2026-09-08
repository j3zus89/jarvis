# DIRECTIVA MAESTRA DE AUDITORÍA Y OPTIMIZACIÓN "JARVIS NIVEL DIOS"
> **Destinatario**: Jules (Google AI Coding Agent)
> **Misión**: Auditoría exhaustiva de arquitectura, rendimiento, latencia de voz, robustez, seguridad y modularidad de JARVIS APEX.
> **Meta final**: Elevar JARVIS a "Nivel Dios" (latencia ultrabaja, cero memory leaks, estabilidad 99.99%, código limpio y modular).

---

## 1. CONTEXTO DEL PROYECTO
JARVIS es un asistente de IA holográfico multimodal con procesamiento local y streaming en tiempo real:
- **Backend**: FastAPI / Python (asyncio) con WebSockets bi-direccionales (`/ws`) para captura de audio PCM 16kHz y streaming TTS.
- **Voz y Audio (STT / TTS)**:
  - STT: Whisper en tiempo real con pasadas incrementales cada ~1.2s y fallback a worker remoto GPU o local.
  - TTS: Servicio dedicado local de clonación de voz XTTS-v2 (`server/xtts/tts_service.py` en puerto 8790) + fallback a Microsoft Edge-TTS, post-procesado con efectos `ffmpeg` en tiempo real.
- **Agente LLM & Sesiones**: Integración con Hermes Sessions API (`POST /api/sessions/{id}/chat/stream`, SSE), gestión de persistencia en `logs/hermes_sessions.json`, tarjetas de aprobación de herramientas y barge-in (interrupción al hablar).
- **AI Router**: Orquestador multi-agente (`server/ai_router/`) para enrutar intenciones y comandos especializados.
- **Frontend HUD**: Interfaz holográfica de partículas 3D en WebGL/Three.js (`server/hud/index.html` y `humanoid3d/`), reactiva a la voz y telemetría de la máquina (`/api/machines`).

---

## 2. OBJETIVOS DE LA AUDITORÍA (CHECKLIST "NIVEL DIOS")

### A. Rendimiento y Concurrencia (Event Loop & Latency)
1. **Detección de Bloqueos en el Event Loop**:
   - Auditar `server/server.py` y `server/xtts/tts_service.py` buscando llamadas síncronas bloqueantes (I/O de disco, llamadas a subprocesos `ffmpeg`, requests HTTP síncronos, o procesamiento numérico).
   - Asegurar que toda tarea pesada utilice `asyncio.to_thread` o ejecutores en thread pool dedicados para no congelar los WebSockets de audio ni generar jitter.
2. **Optimización del Pipeline de Streaming de Voz**:
   - Reducir el Time-to-First-Byte (TTFB) del TTS: fragmentación inteligente de oraciones (sentence splitting) para comenzar la síntesis y el envío de audio PCM mientras Hermes continúa generando tokens.
   - Manejo del buffer de audio WebSocket: prevenir desbordamientos o desincronizaciones de frames PCM int16.
3. **Barge-in (Interrupción) Instantáneo**:
   - Verificar la cancelación inmediata de tareas activas de inferencia y vaciado de buffers de audio cuando el usuario interrumpe al asistente.

### B. Resiliencia, Logs y Gestión de Memoria
1. **Prevención de Explosión de Logs**:
   - Implementar `logging.handlers.RotatingFileHandler` o `TimedRotatingFileHandler` para evitar archivos de log descomunales.
2. **Resiliencia de Procesos y Failover Automático**:
   - Si el servicio XTTS (`tts_service.py` en 8790) cae o no responde en tiempo límite, implementar fallback automático instantáneo y transparente a Edge-TTS sin romper la sesión de voz del usuario.
   - Reintento y reconexión exponencial para WebSockets y llamadas a Hermes.
3. **Fugas de Memoria (Memory Leaks)**:
   - Revisar listeners de eventos huérfanos en WebSockets, generadores SSE que no se cierran al desconectar clientes, y desasignación de buffers de geometría/partículas en el HUD 3D (WebGL Three.js).

### C. Arquitectura y Calidad de Código (Refactor Modular)
1. **Modularización de `server.py`**:
   - `server.py` ha crecido excesivamente en un solo archivo monolítico. Desacoplarlo en una estructura limpia de FastAPI:
     - `server/routers/voice.py` (WebSocket de voz, audio streaming, barge-in)
     - `server/routers/hermes_proxy.py` (API proxy, SSE, sessions)
     - `server/routers/telemetry.py` (máquinas, uso de tokens, HUD stats)
     - `server/services/tts_engine.py` (orquestación XTTS / Edge-TTS / ffmpeg)
     - `server/services/stt_engine.py` (Whisper local/remoto)
2. **Tipado y Validación**:
   - Implementar modelos Pydantic estrictos para payloads WebSocket y endpoints REST.
   - Agregar type hints en funciones críticas.

### D. Seguridad y Sanitización
1. **Redacción de Secretos en Logs y Transmisión**:
   - Asegurar que ninguna clave de API ni token de sesión se emita en eventos WebSocket, payloads de telemetría o logs de depuración.
2. **CORS, Headers y Orígenes WebSocket**:
   - Configurar restricciones estrictas de orígenes permitidos.

### E. HUD Holográfico 3D y Experiencia de Usuario
1. **Optimización de Renderizado WebGL/Three.js**:
   - Garantizar 60 FPS estables en el canvas 3D con uso eficiente de CPU/GPU.
   - Manejo correcto del resize de ventana y modo headless/ahorro de energía cuando la pestaña está en segundo plano.

---

## 3. METODOLOGÍA DE ENTREGA ESPERADA DE JULES
Para ejecutar esta mejora sin introducir regresiones:
1. **Paso 1 - Diagnóstico Detallado**: Generar un reporte `JARVIS_AUDIT_REPORT.md` enumerando:
   - Vulnerabilidades / Bugs críticos encontrados.
   - Puntos de bloqueo en async/concurrencia.
   - Oportunidades de optimización de latencia y modularización.
2. **Paso 2 - Pull Requests Incrementales**:
   - **PR 1: Resiliencia & Logs**: Logging rotativo, manejo robusto de excepciones y failover XTTS -> Edge-TTS.
   - **PR 2: Concurrencia & Latencia**: Eliminación de llamadas síncronas bloqueantes en el bucle de eventos y optimización de streaming de audio.
   - **PR 3: Refactorización Modular**: Desacoplar `server.py` en routers y servicios con validación Pydantic.
   - **PR 4: HUD & Frontend 3D**: Optimización de shaders/partículas y sincronización audio-reactiva.
