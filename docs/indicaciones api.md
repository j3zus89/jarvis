Quiero que modifiques MI JARVIS LOCAL existente. NO quiero rehacerlo desde cero y NO quiero sustituir Hermes.

OBJETIVO PRINCIPAL
Quiero que Jarvis pueda utilizar modelos de IA online mediante APIs gratuitas, manteniendo Hermes como motor local y fallback final.

El problema que quiero solucionar es que no puedo pagar APIs de OpenAI/ChatGPT, Anthropic/Claude, etc. Quiero aprovechar proveedores con Free Tier oficial y hacer que Jarvis pueda cambiar automáticamente de proveedor cuando uno alcance sus límites.

IMPORTANTE:
- No quiero pagar ninguna API.
- No quiero introducir métodos para saltarme límites o restricciones de proveedores.
- No quiero múltiples cuentas creadas artificialmente para evadir límites.
- Solo Free Tiers y APIs que permitan legítimamente su uso gratuito.
- Hermes LOCAL debe seguir funcionando.
- No rompas ninguna funcionalidad actual.
- Antes de modificar código, analiza exhaustivamente el proyecto existente.

==================================================
ARQUITECTURA QUE QUIERO
==================================================

JARVIS
   │
   ▼
AI ROUTER / PROVIDER MANAGER
   │
   ├── 1. GROQ
   │
   ├── 2. GEMINI
   │
   ├── 3. OPENROUTER FREE
   │
   └── 4. HERMES LOCAL
          └── fallback definitivo

El sistema debe utilizar primero el proveedor online más rápido disponible.

Si un proveedor:
- devuelve 429
- alcanza su límite
- devuelve quota exceeded
- devuelve rate limit
- devuelve timeout
- está caído
- devuelve error de API
- no responde

debe pasar automáticamente al siguiente proveedor.

Si todos los proveedores online fallan:
→ utilizar Hermes LOCAL.

Jarvis NUNCA debe quedarse sin motor.

==================================================
PRIORIDAD
==================================================

Prioridad inicial:

1. GROQ
2. GEMINI
3. OPENROUTER FREE
4. HERMES LOCAL

Pero NO des por hecho que estos proveedores/modelos son los mejores actualmente.

Antes de implementarlo:
- comprueba la documentación oficial actual de cada proveedor;
- identifica qué modelos tienen Free Tier actualmente;
- comprueba límites actuales;
- comprueba compatibilidad con OpenAI API si existe;
- comprueba velocidad/latencia;
- comprueba si requieren tarjeta;
- comprueba si el Free Tier es realmente gratuito;
- comprueba las restricciones de uso.

Si alguno ya no cumple las condiciones, sustitúyelo por otro proveedor legítimamente gratuito.

NO uses blogs como fuente principal. Prioriza documentación oficial.

==================================================
REQUISITO CRÍTICO: VELOCIDAD
==================================================

Jarvis utiliza Hermes y quiero que la conversación sea rápida.

Por tanto NO quiero seleccionar modelos simplemente porque tengan muchos parámetros.

Quiero priorizar:

1. baja latencia
2. velocidad de generación
3. calidad de razonamiento
4. límite gratuito
5. estabilidad

Busca el mejor equilibrio.

==================================================
AI ROUTER
==================================================

Crea una capa independiente:

AI Router

Debe recibir algo parecido a:

{
  messages,
  system,
  tools,
  model_preference,
  max_tokens
}

y decidir automáticamente qué proveedor utilizar.

Jarvis/Hermes no debería tener que conocer los detalles de cada proveedor.

Quiero una interfaz común.

Por ejemplo conceptualmente:

router.chat(request)

El router se encarga del resto.

==================================================
FAILOVER
==================================================

Implementa failover real.

Ejemplo:

Groq
 ↓
429
 ↓
Gemini
 ↓
timeout
 ↓
OpenRouter
 ↓
error
 ↓
Hermes local

No quiero que un error de un proveedor rompa la conversación.

El router debe distinguir entre:

- error temporal
- rate limit
- cuota agotada
- API key inválida
- proveedor caído
- timeout
- error permanente

Y actuar correctamente.

==================================================
CONTROL DE CUOTAS
==================================================

Quiero que el sistema registre el consumo de cada proveedor.

Guardar como mínimo:

- requests
- tokens de entrada
- tokens de salida
- tokens totales
- errores
- rate limits
- última utilización
- cuota conocida
- timestamp del último error
- proveedor utilizado

Si la API proporciona headers de rate limit/quota, utilizarlos.

Quiero evitar llegar al límite absoluto.

Por ejemplo:

GROQ
████████░░ 80%

Cuando llegue a un umbral configurable:

→ pasar al siguiente proveedor.

No quiero esperar obligatoriamente al 429.

Los límites deben ser configurables.

==================================================
COOLDOWN
==================================================

Si Groq alcanza temporalmente su límite:

marcarlo como:

COOLDOWN

y no volver a llamarlo durante el tiempo necesario.

Después comprobar automáticamente si vuelve a estar disponible.

Lo mismo para los demás proveedores.

==================================================
CONFIGURACIÓN
==================================================

NO quiero claves API escritas directamente en el código.

Utiliza variables de entorno.

Ejemplo:

GROQ_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=

y cualquier otra necesaria.

Crear/actualizar:

.env.example

pero NUNCA escribir claves reales en Git.

==================================================
MODELOS
==================================================

No elijas modelos a ciegas.

Investiga los modelos gratuitos actuales.

Quiero una tabla interna/documentación:

PROVIDER
MODEL
FREE?
LIMITS
CONTEXT
SPEED
REASONING
TOOLS
STREAMING
OPENAI COMPATIBLE
CARD REQUIRED
STATUS

Después selecciona los mejores.

Especialmente quiero investigar:

Groq
Gemini
OpenRouter
Cerebras
Mistral
otros proveedores gratuitos legítimos que puedan ser mejores

Pero no añadas proveedores por cantidad.

Solo añade proveedores que realmente aporten valor.

==================================================
STREAMING
==================================================

Esto es MUY importante.

Si el proveedor soporta streaming:

utilizar streaming.

No quiero que Jarvis espere 10 segundos y después aparezca toda la respuesta.

Quiero:

token
token
token
token...

en tiempo real.

Si un proveedor no soporta streaming correctamente, debe tener menor prioridad.

==================================================
CONTEXTO Y TOKENS
==================================================

Quiero evitar desperdiciar cuota.

NO enviar todo el historial de Jarvis a cada petición si no es necesario.

Analiza cómo funciona actualmente la memoria/contexto.

Mantén únicamente el contexto necesario para responder.

Si el proyecto ya tiene memoria/RAG/context management:

NO lo reemplaces.

Intégrate con él.

Objetivo:

reducir tokens enviados a APIs online.

==================================================
TAREAS SENCILLAS
==================================================

Quiero considerar routing inteligente.

No todas las preguntas necesitan el modelo online.

Por ejemplo:

"abre Chrome"
"pon música"
"qué hora es"
"ejecuta X"
"abre Discord"

→ Hermes/local/tool system.

Mientras que:

"analiza este problema"
"razona sobre esto"
"explícame..."
"compara..."
"escribe..."
"programa..."

pueden utilizar modelo online.

Pero NO implementes heurísticas peligrosas sin analizar primero cómo funciona Jarvis.

Quiero que aproveches la arquitectura existente.

==================================================
HERRAMIENTAS / FUNCTION CALLING
==================================================

MUY IMPORTANTE.

Jarvis tiene herramientas.

No quiero que al cambiar de proveedor pierda:

- tools
- function calling
- acciones
- memoria
- comandos
- contexto
- streaming

Comprueba qué proveedores/modelos soportan herramientas.

Si un modelo no soporta una herramienta concreta:

hacer fallback o utilizar el modelo apropiado.

==================================================
OBSERVABILIDAD
==================================================

Quiero poder saber qué está pasando.

Crear logs claros:

[AI ROUTER]
Provider: Groq
Model: ...
Input tokens: ...
Output tokens: ...
Latency: ...
Status: SUCCESS

Ejemplo:

[AI ROUTER]
Groq → 429
Gemini → SUCCESS
Latency: 1.2s

No registrar:

- API keys
- secretos
- contraseñas
- datos privados innecesarios

==================================================
PANEL / COMANDO DE ESTADO
==================================================

Si la arquitectura actual lo permite, añade una forma de consultar:

AI STATUS

y mostrar:

Groq: ONLINE
Gemini: ONLINE
OpenRouter: COOLDOWN
Hermes: ONLINE

y estadísticas:

Requests today
Tokens today
Errors today
Current provider
Fallback count

Si Jarvis ya tiene interfaz/dashboard, intégralo ahí.

Si no tiene interfaz, crea primero una función/CLI sencilla en lugar de inventar una UI enorme.

==================================================
SEGURIDAD
==================================================

Nunca:

- hardcodear API keys
- imprimir API keys en logs
- subir .env a Git
- enviar claves al modelo
- guardar secretos en bases de datos sin necesidad

Añadir .env a .gitignore si no está.

==================================================
NO ROMPER EL PROYECTO
==================================================

ANTES DE PROGRAMAR:

1. inspecciona toda la estructura;
2. identifica entrypoint;
3. identifica motor Hermes;
4. identifica sistema de memoria;
5. identifica sistema de herramientas;
6. identifica cómo se realizan actualmente las llamadas al modelo;
7. identifica configuración;
8. identifica dependencias;
9. identifica tests;
10. identifica cualquier arquitectura existente de providers.

NO empieces modificando archivos inmediatamente.

Primero quiero una auditoría.

==================================================
FASE 1 — AUDITORÍA
==================================================

Analiza el proyecto y dime:

- arquitectura actual
- dónde está Hermes
- cómo se comunica Jarvis con Hermes
- dónde añadir el router
- qué archivos habrá que modificar
- qué archivos nuevos crear
- riesgos
- incompatibilidades
- dependencias necesarias

NO hagas cambios todavía.

Después presenta el plan exacto.

==================================================
FASE 2 — IMPLEMENTACIÓN
==================================================

Después de la auditoría:

Implementa:

AI Router
Provider adapters
Groq adapter
Gemini adapter
OpenRouter adapter
Hermes adapter
Failover
Cooldown
Quota tracking
Token tracking
Latency tracking
Streaming
Error handling
.env.example
Logs
Tests

Todo integrado con la arquitectura existente.

==================================================
FASE 3 — TESTS
==================================================

Crear pruebas para:

1. Groq funciona
2. Gemini funciona
3. OpenRouter funciona
4. Hermes funciona
5. Groq falla → Gemini
6. Gemini falla → OpenRouter
7. OpenRouter falla → Hermes
8. timeout
9. 429
10. invalid API key
11. streaming
12. tools/function calling
13. consumo de tokens
14. cooldown
15. recuperación del proveedor

==================================================
FASE 4 — DOCUMENTACIÓN
==================================================

Crear documentación clara:

AI_ROUTER.md

Debe explicar:

- arquitectura
- proveedores
- modelos
- configuración
- API keys
- límites
- failover
- cooldown
- logs
- troubleshooting
- cómo añadir otro proveedor

Quiero que añadir un proveedor nuevo en el futuro sea sencillo.

Idealmente:

providers/
    groq/
    gemini/
    openrouter/
    hermes/

y un sistema común:

BaseProvider

para que añadir:

Cerebras
Mistral
etc.

sea simplemente implementar otro adapter.

==================================================
REGLA FUNDAMENTAL
==================================================

NO quiero una arquitectura dependiente de un proveedor.

Quiero que Jarvis sea independiente del proveedor.

Hoy:

Groq
Gemini
OpenRouter
Hermes

Mañana:

Cerebras
Mistral
otro proveedor

sin modificar el núcleo de Jarvis.

==================================================
RESULTADO FINAL
==================================================

Quiero conseguir esto:

Jarvis permanece LOCAL.
Hermes permanece LOCAL.
Las APIs online son aceleradores externos.
El router decide automáticamente.
Se utiliza primero el proveedor rápido.
Cuando llega al límite → siguiente.
Cuando falla → siguiente.
Cuando todos fallan → Hermes.
Cuando un proveedor se recupera → vuelve automáticamente.
Las cuotas se controlan.
El consumo se registra.
El streaming se mantiene.
Las herramientas se mantienen.
La memoria se mantiene.
No hay costes obligatorios.
No se evaden límites de ningún proveedor.

IMPORTANTE:

NO asumas nada sobre mi proyecto.

Primero AUDITA.

No cambies código hasta terminar la auditoría y explicarme exactamente qué vas a modificar.