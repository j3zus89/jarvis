# Apex Interface Brief — la parte visual (aparte, fase posterior)

Este documento es independiente de `APEX_UPGRADE_BRIEF.md` a propósito. No se
lo pases a Claude Code junto con el prompt de inteligencia — es una fase
posterior, visual, que solo tiene sentido una vez las Fases 1-4 de aquel
documento ya funcionen. Mezclar los dos hace que se trabaje en la cara antes
que en el cerebro, que es justo lo que no queríamos.

Todo lo de abajo es lo que se ve literalmente en los 7 vídeos de Apex
(capturas de pantalla + transcripción), no invención.

## Dos modos de interfaz, no uno

Apex tiene **dos vistas distintas**, y la segunda no es la vista por defecto —
hay que pedirla por voz explícitamente.

### 1. Vista normal (dashboard) — la que está siempre activa

- **Barra superior**: hora (HH:MM grande), temperatura/clima, fecha, línea
  "ON THIS DAY" con un dato histórico del día, saludo personalizado
  ("Good evening, Ruben").
- **Indicadores de estado**: tres puntos de color — `● APEX  ● LOCAL  ● VOICE`
  (verde = conectado, rojo = según el estado, p.ej. voz inactiva). Debajo,
  una línea de estado tipo "Awaiting your command" / "Lead: Ruben".
- **El orbe central**: círculo grande con anillo dorado y núcleo de
  partículas cian, animado. Cambia de estado visualmente y por texto debajo:
  `LISTENING` / `SPEAKING` / `STANDBY`, con una onda de audio (barras) que
  reacciona en tiempo real cuando habla.
- **Mapa orbital alrededor del núcleo**: nodos pequeños conectados por líneas
  curvas animadas, cada uno con etiqueta — son los "agentes" por rol:
  `Strategist, Researcher, Chief of Staff, Sales, Marketing, Ops, Social,
  CRM, Engineering, Developer, Analytics, Finance, Design, Editor, Memory,
  Email, Calendar, Drive`. Color ámbar para roles de negocio, cian para
  roles de infraestructura/core — es puramente visual, no cambia función.
- **Menú lateral** (colapsable, píldoras redondeadas con icono, glow
  verde/teal, fondo semitransparente): en la versión que capturaste —
  `Goals & Revenue, Leads, Tasks, Projects, Social Analytics, Publish to
  Instagram, Publish to LinkedIn, Calendar, Guide, Agents, Activity`. En una
  versión anterior también aparecía `Notifications, Apex's Briefing,
  Engineering, Loops, Ad Campaigns, Dev Log` — probablemente se consolidaron.
- **Vista de respuesta larga**: cuando pides algo como el reporte semanal, el
  orbe se sustituye por un panel de texto (estilo markdown) con la respuesta
  completa de Apex, botones de acción pequeños abajo (imagen, compartir) y un
  campo `Ask Apex...`.

### 2. Vista "humanoide" (la cara) — modo separado, invocado por voz

No está activa por defecto. Se abre con un comando de voz explícito:

> *"Show me your face"* / *"Open humanoid"* → Apex responde *"Opening the
> humanoid view"* — hay una transición/animación de apertura, no es instantáneo.

Visualmente:

- Un busto (cabeza + hombros) hecho **enteramente de partículas** — miles de
  puntos brillantes en azul, sin geometría sólida, sin textura de piel ni
  rasgos realistas. Es una nube de puntos con forma humana, no un avatar 3D
  renderizado.
- Un núcleo naranja/dorado brillante concentrado en la zona de la cara/pecho
  — simula algo como una "voz" o "core" pulsante, con anillos concéntricos
  finos que se expanden hacia afuera como ondas de sonido.
- Etiqueta de estado en la esquina: `STATUS: LISTENING` / `STATUS: SPEAKING`.
- En el setup físico de Ruben esto vive en **un monitor dedicado aparte** (un
  tercer monitor solo para esto, no comparte pantalla con el dashboard) — pero
  la lógica de "modo separado invocado por voz" es lo que importa portar, el
  monitor físico es indiferente para ti.

## Lo técnico, en tus términos (server/hud/index.html)

Nada de esto requiere una librería 3D pesada ni fotorrealismo:

- Es un **sistema de partículas en canvas/WebGL** (Three.js con
  `THREE.Points`, o incluso canvas 2D con miles de puntos si quieres evitar
  una dependencia nueva) posicionadas en una forma de busto fija o generada
  desde una imagen/máscara.
- El pulso naranja y las ondas reaccionan a **audio en tiempo real** — tu HUD
  ya captura audio a 16kHz y ya tiene el pipeline de TTS streaming
  (docs/ARCHITECTURE.md); conectar un `AnalyserNode` de Web Audio a la
  amplitud de la reproducción TTS y mapear eso a la intensidad/tamaño de las
  partículas es todo el "truco". No hay reconocimiento facial ni generación
  de imagen en vivo.
- El toggle "vista normal ↔ vista humanoide" es un simple cambio de estado en
  el HUD (como ya tienes con las cinematic boot / holographic media panels),
  disparado por un comando de voz que reconozcas por keyword ("muéstrame la
  cara", "abre el modo humanoide") igual que ya haces con `_try_gmail_action`.

## Por qué va después

Esto es 100% percepción, cero inteligencia. Es la parte más fácil de admirar
en un vídeo de 30 segundos y la más barata de construir bien una vez el
cerebro (Fases 1-4 del otro documento) ya funciona — hazlo al final o te vas
a quedar con una cara bonita hablando con un modelo de 14B.
