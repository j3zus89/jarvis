# Apex Upgrade Brief — prompt y guía para Claude Code

Este documento es el briefing de arranque para la sesión de Claude Code que va a
llevar este repo (`jarvis-hud-hermes`) al nivel de inteligencia observado en un
sistema de referencia con nombre en clave "Apex" (analizado solo por vídeo, no
tenemos su código).

Vive dentro del repo a propósito: cuando muevas las carpetas, este archivo viaja
con `jarvis-hud-hermes` y Claude Code lo puede leer directamente al arrancar.

> **Nota de esta revisión:** este brief incorpora tres correcciones sobre la
> versión anterior — verificación de si la Fase 1 ya está hecha, un GO-gate por
> niveles de riesgo en vez de binario, y verificación mínima en standing
> orders. Se explican en el propio texto de cada fase, marcadas como tal.

## 0. Antes de abrir Claude Code

1. Crea `C:\Jarvis Apex\`.
2. Mueve dentro, **como hermanas, sin anidar una dentro de otra**:
   - `C:\jarvis-hud-hermes` → repo de trabajo, el único activo
   - `C:\jarvis-hermes-dashboard` → solo referencia (puede tener lógica de voz reusable)
   - `C:\Jarvis-hud` → solo referencia (proyecto anterior, más documentado)
3. **NO muevas `C:\Users\Jesus\.hermes`**. Es la instalación real de Hermes
   Agent (cron, sesiones, kanban, tokens OAuth, gateway corriendo). Vive en
   `%USERPROFILE%\.hermes` por convención y moverla puede romper el gateway.
   Se queda donde está; el repo ya le apunta por API, no por filesystem.
4. Abre una terminal **dentro de** `C:\Jarvis Apex\jarvis-hud-hermes` (no en la
   carpeta padre — es la que tiene el `.git`) y arranca `claude` ahí.

## 1. El prompt para pegar en Claude Code

```
Contexto del proyecto:
Este repo (jarvis-hud-hermes, aquí en C:\Jarvis Apex\jarvis-hud-hermes) es un
asistente de voz tipo Jarvis construido sobre Hermes Agent. El backend de
Hermes Agent ya está instalado y corriendo en C:\Users\Jesus\.hermes (cron,
sesiones, memoria, skills) — no lo muevas, no toques su configuración salvo
para leerla si hace falta.

En la misma carpeta padre (C:\Jarvis Apex\) hay otras dos carpetas de intentos
anteriores: jarvis-hermes-dashboard y Jarvis-hud. NO son el proyecto activo.
Solo ábrelas si buscas código reutilizable (p.ej. su manejo de voz/TTS) y
siempre pregunta antes de portar algo de ahí a este repo.

Léete primero README.md y docs/ARCHITECTURE.md completos de este repo antes
de tocar nada — ahí está el protocolo WebSocket, los endpoints, y once
"gotchas" ya resueltos que no hay que reintroducir. Lee también docs/AI_ROUTER.md
y docs/JOB_AGENT.md — ya existe un AI Router con cost guard real
(CostCappedProvider) y un patrón de agente especializado (JOB_AGENT) que hay
que replicar, no rediseñar.

Objetivo:
Llevar la INTELIGENCIA de este asistente (no la interfaz visual) al nivel de
un sistema de referencia que observamos en vídeo (nombre en clave "Apex").
Apex tiene, en orden de impacto real sobre "se siente inteligente":

1. Un modelo de razonamiento fuerte detrás (no un modelo local pequeño) —
   esto es la causa raíz de que el asistente actual "no sea inteligente del
   todo" si es que sigue corriendo sobre un modelo local. VERIFICAR PRIMERO
   (ver Fase 1 — puede que esto ya esté resuelto).
2. Standing orders: tareas que corren solas sin que el usuario hable
   (brief matutino, resumen de correo periódico, etc.), programadas.
3. Loops Engine: motor de flujos semi-autónomos recurrentes que ajustan su
   comportamiento con el feedback del usuario (no solo cron fijo).
4. Sistema GO-gated por niveles de riesgo: separa "aconsejar" de "ejecutar".
   No es un ALLOW/DENY plano para todo — ver Fase 3 para la clasificación
   exacta.
5. Self-review: checklist de auto-diagnóstico honesto — sesión Hermes viva,
   tokens de integraciones (Gmail, Calendar, etc.) válidos, loops corriendo,
   uptime, errores desde el último despliegue. Debe poder fallar y decirlo.
6. Activity log: registro auditable append-only de qué hizo el agente,
   cuándo, y por qué — separado del self-review (ese es salud; esto es
   historial).

Fases (implementar en este orden, cada una debe funcionar end-to-end antes
de pasar a la siguiente — no adelantar trabajo de una fase futura):

FASE 1 — Cerebro

**Antes de cambiar nada: comprobar si esto ya está hecho.**
docs/JOB_AGENT.md ("Known limitation") indica que el modelo primario de
Hermes ya se movió a Groq y que eso resolvió el encadenado de tool calls
(job_profile → web_search → job_match), que antes fallaba con el modelo
local. Verificar el valor de `model:` en `C:\Users\Jesus\.hermes\config.yaml`
antes de tocar nada:
- Si ya apunta a Groq (o a otro proveedor online fuerte): esta fase pasa a
  ser "confirmar y documentar en README que ya está resuelto", no "cablear
  de cero". No reintroducir el modelo local como primario sin motivo.
- Si todavía apunta a un modelo local (14B/20B): entonces sí, decidir y
  cablear el proveedor de razonamiento fuerte para Hermes vía su
  configuración de LLM provider. El modelo local queda como fallback o para
  tareas triviales, no como cerebro principal. Esto es config, no reescribir
  arquitectura.

FASE 2 — Standing orders

Un scheduler ligero en server.py (o aprovechando el endpoint /api/hermes/jobs
si Hermes ya soporta cron — comprobarlo primero) que dispare periódicamente
funciones ya existentes como _gmail_inbox_brief() sin intervención del
usuario, y empuje el resultado a la HUD por el mismo canal que ya usa
/api/summon (WebSocket), incluyendo audio TTS.

**Verificación mínima obligatoria (sin esto, la fase no está completa):**
cada standing order debe registrar, además del resultado, si ese resultado
pasó al menos una comprobación determinista antes de notificarse por HUD/voz.
Ejemplo concreto: en el brief de correo, distinguir "0 emails nuevos porque
de verdad no hay correo" de "0 emails nuevos porque el token OAuth caducó y
la llamada falló en silencio". No hace falta un Judge con LLM — con marcar
cada resultado como `ok` / `ok_vacio` / `fallo_silencioso` es suficiente en
esta fase. Es el mismo patrón que ya existe en el código de audio silencioso
(docs/TROUBLESHOOTING.md, "No clip timestamps found") aplicado aquí.

FASE 3 — GO-gate generalizado, por niveles de riesgo

Extender el mecanismo de approval cards que ya existe (hoy solo para
comandos de shell peligrosos) para que cualquier acción disparada por un
standing order/loop pase por una clasificación de riesgo antes de
ejecutarse — NO un ALLOW/DENY plano para todo:

  LOW      → ejecutar sin pausa (ej. leer un correo, consultar RAM/CPU)
  MEDIUM   → ejecutar y notificar después, sin bloquear (ej. escribir un
             archivo local, actualizar el kanban)
  HIGH     → approval card ANTES de ejecutar, igual que hoy con shell
             (ej. enviar un email, comando de terminal, tocar configuración)
  CRITICAL → approval card + si se deniega, NO reintentar automáticamente
             (ej. borrar algo, tocar credenciales/tokens OAuth, cualquier
             acción con dinero real si algún día existe)

Cada standing order/loop declara su propio nivel de riesgo al registrarse
(no se infiere en tiempo de ejecución — eso sería una alucinación más de la
que protegerse). Un GO-gate que pausa todo por igual entrena al usuario a
pulsar ALLOW sin mirar por hartazgo; escalarlo por riesgo real es lo que de
verdad protege.

FASE 4 — Self-review + Activity log

Un endpoint /api/selfcheck que corra una lista de comprobaciones reales
(sesión Hermes, tokens OAuth, loops activos, errores recientes) y devuelva
honestamente qué falla. Un log append-only (archivo o sqlite, lo que ya use
el proyecto) de acciones autónomas ejecutadas, visible desde la HUD —
incluyendo, para cada una, el nivel de riesgo con el que se clasificó
(Fase 3) y si requirió aprobación.

FASE 5 — HUD: paneles nuevos

Panel "Activity" y panel "Agents"/estado de integraciones en el HUD,
reusando el patrón visual ya existente (server/hud/index.html es un solo
archivo vanilla JS, sin build step — mantenlo así).

FASE 6 — Voz (PENDIENTE, no implementar todavía)

Hoy el TTS es ElevenLabs (streaming, funciona). El usuario va a instalar más
adelante un servidor de voz gratuito distinto que aún está evaluando. NO
elijas ni instales ningún proveedor nuevo ahora. Lo único que sí puedes hacer
en esta fase, si tocas el pipeline de TTS por cualquier otro motivo, es dejar
el punto de integración claramente aislado (una función/adapter, no llamadas
a la API de ElevenLabs esparcidas) para que cambiar de proveedor después sea
un cambio localizado. Si no vas a tocar TTS por otras fases, no lo toques.

Restricciones duras:
- No reescribas lo que ya funciona (sesiones Hermes, proxy allowlist, approval
  cards, _gmail_inbox_brief, protocolo WebSocket, AI Router y su
  CostCappedProvider) — extiende, no reemplaces.
- No introduzcas dependencias nuevas si algo ya instalado o del stdlib lo
  resuelve.
- No añadas ningún proveedor de IA nuevo al AI Router (incluido GPT-6 Astra
  o cualquier modelo "premium") en estas fases — sigue fuera de alcance
  hasta que se decida explícitamente en una sesión aparte.
- Antes de cualquier cambio irreversible (mover carpetas, borrar código,
  tocar ~/.hermes, cambiar credenciales) pregunta primero.
- Cada fase termina con una verificación real (arrancar el server, probar el
  flujo) antes de pasar a la siguiente, no solo "código escrito".

Primer paso ahora mismo: lee README.md, docs/ARCHITECTURE.md, docs/AI_ROUTER.md
y docs/JOB_AGENT.md completos. Antes de escribir una sola línea, comprueba:
(a) qué modelo tiene configurado Hermes ahora mismo en
C:\Users\Jesus\.hermes\config.yaml (ver nota de la Fase 1), y (b) si Hermes
Agent ya soporta cron jobs de verdad (mira /api/hermes/jobs y esa misma
config). Luego proponme un plan detallado solo de la FASE 1 con lo que hayas
encontrado, y espera mi confirmación antes de tocar código.
```

## 2. Qué esperar de cada fase (para ti, no para Claude Code)

- **Fase 1** puede resultar ser solo una verificación de una tarde (si ya se
  movió a Groq) o una tarde de config real (si no). Cualquiera de las dos
  formas, no debería llevar más de eso.
- **Fases 2-4** son la carne real del proyecto — varias sesiones de trabajo.
- **Fase 5** es la parte visible pero menos crítica; no dejes que se adelante.
- **Fase 6** queda abierta a propósito: cuando decidas el servidor de voz
  gratuito, vuelve a este documento y dale a Claude Code solo esa fase.

## 3. Decisiones que siguen abiertas y no están en este brief a propósito

- **GPT-6 Astra**: no está en ningún archivo real del proyecto todavía.
  Decide si quieres añadirlo como quinto proveedor del AI Router (con su
  propio CostCappedProvider y presupuesto separado) en una sesión aparte,
  específica para eso — no se cuela en las Fases 1-6 de este brief.
- **instrucciones_herrameintas.md e indicaciones_api.md**: no se los des a
  Claude Code en esta sesión. Son la conversación que llevó a decidir usar
  Hermes Agent en vez de construir un Tool Registry/Permission Manager
  propio — ya cumplieron su función. Si en el futuro Claude Code necesita la
  escala de riesgo LOW/MEDIUM/HIGH/CRITICAL con más detalle del que hay en
  la Fase 3 de arriba, esos archivos la tienen completa.
