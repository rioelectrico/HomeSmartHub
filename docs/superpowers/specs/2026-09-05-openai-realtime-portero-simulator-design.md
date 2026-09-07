# Portero Realtime y simulador fiel de ESP32

**Fecha:** 2026-09-05
**Estado:** diseño SIM-1/SIM-2 aprobado, listo para plan de implementación

## 1. Objetivo

Agregar una pantalla web independiente que se comporte como el futuro portero ESP32-P4 y
permita validar una visita por voz de extremo a extremo sin reemplazar componentes que ya
funcionan ni romper compatibilidad con MVP1:

1. autenticación de dispositivo mediante el challenge/HMAC existente;
2. timbre e inicio de una conversación manos libres;
3. audio bidireccional entre dispositivo, backend y OpenAI Realtime;
4. detección automática de voz e interrupción del agente;
5. transcripción durable de visitante y agente;
6. cierre y resultado durable de la conversación;
7. en una segunda etapa, captura de la persona y registro estructurado de la visita.

El recorrido obligatorio es:

```text
Simulador web -> WebSocket autenticado -> FastAPI -> OpenAI Realtime
OpenAI Realtime -> FastAPI -> WebSocket autenticado -> Simulador web
```

El navegador no se conectará directamente a OpenAI. Todo lo que pueda ser compartido con
el firmware futuro atravesará el mismo límite lógico dispositivo/backend.

## 2. Entrega por etapas

La implementación se divide formalmente en dos hitos. No se inicia SIM-2 hasta completar
y verificar todos los criterios de aceptación de SIM-1.

### SIM-1: conversación de voz extremo a extremo

Debe entregar autenticación real del dispositivo, conversación manos libres, captura y
reproducción PCM16, VAD, barge-in, transcripciones finales durables, límites, cleanup y
pruebas deterministas con `FakeRealtimeProvider`.

### SIM-2: cámara, herramientas y administración avanzada

Después de aprobar SIM-1 agrega webcam/patrón de prueba, `capture_visitor_image`,
`record_visit`, persistencia extendida, listado y detalle administrativo y las cinco
correcciones visuales detectadas durante la aceptación del MVP1.

## 3. Alcance

### Incluido

- Ruta independiente `/simulador-portero`, fuera del layout administrativo.
- Configuración en memoria con `device_id` y secreto del dispositivo.
- Autenticación HMAC y presencia mediante el mismo WebSocket del dispositivo.
- Captura y reproducción de audio manos libres en el navegador.
- Proveedor real OpenAI Realtime encapsulado en el backend.
- VAD del proveedor y barge-in.
- Persistencia mínima de conversaciones y transcripciones finales en SIM-1.
- Webcam real, patrón de prueba, herramientas y persistencia extendida en SIM-2.
- Listado y detalle administrativo de conversaciones en SIM-2.
- Eventos, auditoría, estadísticas y legibilidad operativa corregidos al cerrar SIM-2.
- Pruebas deterministas con proveedor falso y smoke test externo voluntario.

### Excluido

- Apertura de puerta o cualquier comando de acceso.
- Almacenamiento de audio crudo.
- Implementación del transporte en firmware ESP32.
- Llamadas telefónicas, SIP, notificaciones a residentes o reconocimiento facial.
- Ejecución del smoke test real dentro de la suite predeterminada.
- Cerraduras, relés, motores o comandos de acceso físico.

## 4. Decisiones principales

- El simulador debe probar el recorrido que usará el hardware, por lo que el audio no se
  conectará directamente desde el navegador a OpenAI.
- El backend será el único custodio de `OPENAI_API_KEY`.
- JSON continuará transportando autenticación, heartbeat y control; los bytes de audio
  usarán frames binarios versionados.
- La conversación usará PCM16 mono a 24 kHz y frames nominales de 20 ms.
- El modelo efectivo será configurable. `env-default` resolverá
  `OPENAI_REALTIME_MODEL`; el valor inicial será `gpt-realtime-2.1`.
- Cada dispositivo tendrá como máximo una conversación activa.
- La duración máxima inicial será de 300 segundos.
- El agente nunca recibirá una herramienta de apertura.

La selección del modelo sigue la documentación oficial vigente de
[GPT-Realtime-2.1](https://developers.openai.com/api/docs/models/gpt-realtime-2.1), que
soporta audio, razonamiento configurable y herramientas. Aunque OpenAI también permite
WebRTC, esta arquitectura elige WebSocket dispositivo-backend y Realtime desde el backend
para validar el futuro límite del ESP32 y mantener la API key sólo en el servidor.

## 5. Arquitectura

```text
Micrófono/webcam
      |
      v
Simulador web independiente
  - HMAC de dispositivo
  - AudioWorklet
  - framing binario
      |
      | WebSocket autenticado: JSON + binario
      v
FastAPI
  - registro de conexiones
  - coordinador de conversación
  - persistencia y auditoría
  - adaptador AIRealtimeProvider
      |
      | Realtime WebSocket, API key sólo en servidor
      v
OpenAI Realtime
```

El coordinador de conversación no dependerá del SDK concreto. Consumirá una interfaz
asíncrona `AIRealtimeProvider` que permita abrir una sesión, enviar audio, recibir eventos,
cancelar salida y cerrar. La implementación real y el proveedor falso deberán respetar el
mismo contrato.

## 6. Pantalla del simulador

### 6.1 Configuración

La ruta `/simulador-portero` mostrará:

- identificador con formato `PI-000001`;
- secreto en un campo de contraseña;
- botón `Conectar`;
- aviso de que el secreto sólo vive en memoria.

El secreto no se persistirá en cookies, `localStorage`, `sessionStorage`, URLs, telemetría
ni logs. Al cerrar o recargar la pestaña deberá ingresarse nuevamente.

### 6.2 Portero conectado

Después de autenticarse mostrará:

- identificador y estado de conexión;
- botón principal `Tocar timbre`;
- estados `Preparando`, `Escuchando`, `Visitante hablando`, `Agente hablando` y `Error`;
- nivel visual del micrófono;
- transcripción diferenciada por participante;
- botón `Finalizar visita`.

La interacción de audio comienza únicamente al tocar el timbre. Ese gesto habilita el
permiso de micrófono y la reproducción de audio para respetar las políticas de permisos y
autoplay del navegador.

### 6.3 Extensión de cámara en SIM-2

Sólo después de aprobar SIM-1, la configuración agregará la elección explícita `Webcam
real` o `Patrón de prueba` y la vista conectada mostrará el estado de captura. En modo
webcam, el simulador capturará el frame actual y lo codificará como JPEG dentro del límite
existente. En modo patrón, usará una imagen identificada visualmente y marcará
`source=test_pattern` en el resultado. Si la cámara no está disponible, la herramienta
fallará de forma explícita; nunca se sustituirá silenciosamente por una imagen ficticia.

## 7. Comportamiento del agente

Las instrucciones efectivas combinarán reglas no anulables del sistema con la
configuración editable del hogar.

Reglas no anulables:

- saludar y mantener respuestas breves, cordiales y apropiadas para un portero;
- solicitar nombre y motivo de la visita;
- no afirmar que abrió o puede abrir la puerta;
- no inventar confirmaciones de residentes;
- usar sólo las herramientas declaradas en la etapa activa;
- cerrar cortésmente ante abuso, silencio prolongado o límite de duración.

SIM-1 no declara ninguna herramienta de acceso físico ni las herramientas propias de
SIM-2. El agente conversa, obtiene nombre y motivo, maneja silencio prolongado y finaliza
educadamente.

### Herramientas de SIM-2

### `capture_visitor_image`

Sin argumentos sensibles. El backend envía `camera.capture` al mismo dispositivo, espera
ACK, resultado y media durable, y devuelve al modelo sólo estado, identificador de media y
un error público opcional. El timeout no dejará bloqueada la conversación.

### `record_visit`

Argumentos validados:

- `visitor_name`: 1–128 caracteres;
- `reason`: 1–1000 caracteres;
- `outcome`: `registered`, `declined`, `abandoned` o `failed`;
- `summary`: 1–2000 caracteres.

La operación será idempotente por `conversation_id` y `tool_call_id`. Una finalización sin
llamada válida conservará un resultado seguro derivado por el backend.

## 8. Extensión del protocolo del dispositivo

### 8.1 Compatibilidad

El handshake JSON existente conservará su versión. El dispositivo anunciará la capacidad
opcional `audio_pcm16_v1`. Un cliente sin esa capacidad seguirá funcionando como en MVP1.
El backend rechazará mensajes de conversación de un cliente que no la anunció.

El contrato queda preparado para anunciar en el futuro `camera_jpeg_v1`, `speaker_v1` y
`microphone_v1`, pero SIM-1 no implementará capacidades innecesarias.

### 8.2 Control JSON

Se agregarán mensajes versionados y discriminados:

- dispositivo → servidor: `conversation.start`, `conversation.stop`;
- servidor → dispositivo: `conversation.started`, `conversation.state`,
  `conversation.transcript`, `conversation.audio.clear`, `conversation.ended`;
- ambos sentidos: errores estables con código y `correlation_id`.

SIM-2 agrega `conversation.tool` sin cambiar las formas de SIM-1.

`conversation.start` no acepta instrucciones arbitrarias ni modelos enviados por el
dispositivo. El backend resuelve hogar, configuración, límites y modelo desde datos
autorizados.

Formas mínimas de los mensajes nuevos:

```json
{"type":"conversation.start","version":1,"seq":21,"mode":"hands_free"}
{"type":"conversation.stop","version":1,"seq":22,"conversation_id":"uuid","reason":"visitor_finished"}
{"type":"conversation.started","version":1,"seq":40,"conversation_id":"uuid","stream_id":"uuid","audio":{"codec":"pcm16","sample_rate":24000,"channels":1,"frame_ms":20},"max_seconds":300}
{"type":"conversation.state","version":1,"seq":41,"conversation_id":"uuid","state":"listening"}
{"type":"conversation.transcript","version":1,"seq":42,"conversation_id":"uuid","role":"assistant","text":"Buen día.","final":true}
{"type":"conversation.audio.clear","version":1,"seq":43,"conversation_id":"uuid","stream_id":"uuid","reason":"barge_in"}
{"type":"conversation.ended","version":1,"seq":44,"conversation_id":"uuid","outcome":"registered","reason":"visitor_finished"}
```

Los UUID de ejemplo representan strings UUID canónicos. `mode` sólo acepta `hands_free`.
Los estados son `preparing`, `listening`, `visitor_speaking`, `assistant_speaking` y
`error`. Las razones y errores serán enums cerrados documentados junto con el código.
Texto, IDs, secuencias y enums usarán los mismos límites estrictos y rechazo de campos
extra que el protocolo actual.

El dispositivo no puede enviar prompt, instrucciones, modelo, voz, configuración de
herramientas ni un identificador arbitrario de hogar. El backend resuelve esos valores
desde la identidad autenticada y la configuración autorizada.

### 8.3 Header binario

Todo frame tendrá el siguiente header big-endian de 34 bytes:

```text
magic[4] | version:u8 | type:u8 | stream_id:uuid[16] | seq:u64be |
payload_length:u32be | payload[payload_length]
```

- `magic`: ASCII `PAUD`;
- `version`: `1`;
- `type=1`: audio PCM16 de dispositivo a servidor;
- `type=2`: audio PCM16 de servidor a dispositivo;
- `stream_id`: UUID efímero del stream de audio, distinto de `conversation_id`;
- `seq`: secuencia estrictamente creciente por dirección y stream, independiente de la
  secuencia JSON y comenzando en uno para cada stream;
- `payload_length`: longitud exacta restante.

El payload nominal de entrada será de 960 bytes: 480 muestras, 20 ms, PCM16 mono a 24 kHz.
El parser rechazará antes de entregar bytes al proveedor: magic, versión o tipo inválidos;
payload vacío, impar o demasiado grande; longitud declarada distinta de la recibida;
stream desconocido o no perteneciente al socket; y secuencia duplicada o fuera de orden.
También rechazará frames binarios recibidos antes de autenticar o sin la capacidad
`audio_pcm16_v1`.

`conversation_id` identifica el registro lógico y durable durante toda su vida.
`stream_id` identifica exclusivamente una instancia efímera de transporte de audio. Un
stream futuro podría reconstruirse o reemplazarse sin cambiar `conversation_id`; cada
stream nuevo reinicia sus secuencias binarias por dirección en uno. En SIM-1 existe un
único stream por conversación, pero los dos identificadores nunca son intercambiables.

### 8.4 Flujo y backpressure

- Cola de entrada máxima: 50 frames nominales.
- Cola de salida máxima: 100 frames nominales.
- Un overflow de entrada cerrará sólo la conversación con `AUDIO_INPUT_OVERFLOW`.
- Si la cola de salida alcanza 100 frames, el coordinador cancelará la respuesta activa,
  vaciará toda la salida pendiente, enviará `conversation.audio.clear` al dispositivo y
  cerrará la conversación con `AUDIO_OUTPUT_OVERFLOW`.
- El barge-in cancelará la respuesta activa en OpenAI, vaciará la cola de salida del
  backend, enviará `conversation.audio.clear` y hará que el AudioWorklet descarte de
  inmediato el audio local todavía no reproducido.
- Un cliente lento no podrá acumular memoria sin límite.
- Los bytes de audio nunca se escribirán en logs ni base de datos.

Un overflow de entrada o salida cierra sólo la conversación; el dispositivo puede
permanecer autenticado y online. SIM-1 no intenta recuperar una salida saturada porque
continuar agregaría latencia y haría que el portero hablara fuera de tiempo. Las secuencias
binarias son independientes en cada dirección y de la secuencia JSON.

## 9. Integración OpenAI Realtime

La implementación real abrirá una sesión Realtime desde el backend y configurará:

- modelo efectivo del agente;
- instrucciones efectivas;
- voz y velocidad compatibles;
- audio PCM16 de entrada y salida;
- detección automática de turnos;
- transcripción de entrada;
- herramientas estrictas de esta especificación;
- límite de salida y truncación razonable.

El coordinador consumirá el contrato conceptual `open_session`, `send_audio`,
`receive_events`, `cancel_response` y `close`. El adaptador real convertirá eventos
externos a eventos internos tipados; no propagará payloads crudos, nombres de eventos de
OpenAI ni detalles del transporte. `FakeRealtimeProvider` implementará el mismo contrato
para pruebas deterministas sin red.

El diagnóstico distinguirá:

- `unconfigured`: ausente, vacío o placeholder conocido;
- `configured`: configuración plausible, sin llamada externa;
- `available`: smoke test explícito exitoso;
- `unavailable`: smoke test explícito fallido, con detalle público seguro.

La suite normal usará un proveedor falso. El smoke test real será un comando separado,
opt-in, con duración breve, sin audio persistido y advertencia de consumo.

El inicio respeta este orden transaccional:

```text
conversation.start
        |
        v
validar identidad, capacidad, configuración y ausencia de otra conversación
        |
        v
abrir sesión OpenAI Realtime
        |
        v
crear conversación durable y asignar stream efímero
        |
        v
conversation.started
```

Si la configuración no es válida o la sesión Realtime no abre, el backend cierra cualquier
recurso parcial, devuelve `AI_UNCONFIGURED` o `AI_UNAVAILABLE` y no crea una conversación
durable. Si falla la persistencia después de abrir Realtime, cierra inmediatamente la
sesión externa y tampoco emite `conversation.started`.

## 10. Persistencia

### 10.1 Persistencia mínima de SIM-1

Cada inicio que logra abrir Realtime crea exactamente una conversación durable ligada al
dispositivo y al socket autenticado. Una solicitud rechazada durante las validaciones o la
apertura externa no deja registros incompletos. La primera migración agrega únicamente lo
requerido por voz:

- `device_id`, FK nullable con `ON DELETE SET NULL` para conservar datos históricos;
- `outcome`, enum nullable mientras la conversación esté abierta.

Se conservan `created_at`, `closed_at`, `home_id`, `status` y las relaciones existentes.
Los mensajes guardan sólo transcripciones finales con `role=user` para el visitante y
`role=assistant` para el agente. Los deltas parciales no se guardan como mensajes y cada
ítem final del proveedor se deduplica antes de persistir.

No se persisten audio, secretos, credenciales, IDs de sesión externos ni payloads externos
crudos.

### 10.2 Persistencia extendida de SIM-2

Una segunda migración, posterior a la aprobación de SIM-1, agrega:

- `visitor_name`, string nullable de 128;
- `visit_reason`, texto nullable;
- `summary`, texto nullable;
- `capture_media_id`, FK nullable con `ON DELETE SET NULL`.

`created_at` y `closed_at` permiten derivar duración. No se guardarán API session IDs ni
credenciales externas.

## 11. Cámara y herramientas de SIM-2

SIM-2 reutilizará el protocolo y la infraestructura de media de MVP1. Si ya existe un
intercambio equivalente a `camera.capture`, ACK y resultado correlacionado, se extenderá
ese contrato en lugar de duplicarlo. La captura real será JPEG dentro de los límites de
media existentes; el patrón de prueba se identificará con `source=test_pattern`.

`capture_visitor_image` enviará la orden al mismo socket, esperará media durable con timeout
y devolverá al modelo sólo `status`, `media_id` y un `public_error` opcional. Un fallo de
cámara no cerrará por sí solo la conversación.

`record_visit` validará `visitor_name` de 1–128 caracteres, `reason` de 1–1000,
`summary` de 1–2000 y `outcome` en `registered`, `declined`, `abandoned` o `failed`. Será
idempotente por `conversation_id + tool_call_id`.

## 12. API administrativa de SIM-2

Se agregarán endpoints protegidos y acotados al hogar:

- `GET /api/homes/{home_id}/conversations` con cursor, fechas, dispositivo y resultado;
- `GET /api/homes/{home_id}/conversations/{conversation_id}` con mensajes y referencia a
  la captura.

Se reutilizará `events.read` para listar conversaciones y `camera.view` para obtener la
imagen. La ausencia de `camera.view` omitirá la referencia de media sin filtrar su URL.
No se crearán endpoints administrativos para iniciar conversaciones en nombre de un
dispositivo.

## 13. Seguridad y privacidad

- La API key de OpenAI sólo se leerá desde configuración del backend.
- Claves ausentes, placeholders o inválidas no aparecerán como integración disponible.
- Un dispositivo sólo podrá iniciar conversaciones para su hogar y su propia identidad.
- Una conversación quedará ligada a la conexión autenticada que la abrió.
- Reemplazo de socket, deshabilitación o rotación de credencial cerrará la conversación.
- Máximo una conversación activa por dispositivo.
- Duración máxima predeterminada de 300 segundos e inactividad máxima de 45 segundos.
- Todos los inputs de herramientas se validarán y acotarán.
- Logs y errores excluirán secretos, cookies, API keys, audio y payloads externos crudos.
- Transcripciones y capturas respetarán RBAC y tenancy existentes.
- No existe herramienta ni comando de apertura en esta entrega.

## 14. Errores y recuperación

- Fallo de permisos de navegador: mensaje accionable y reintento sin recargar.
- Fallo de autenticación: cerrar socket y borrar el secreto en memoria.
- OpenAI no configurado: no crear conversación durable y devolver `AI_UNCONFIGURED`.
- Fallo al abrir proveedor: cerrar conversación como `failed` y mantener dispositivo online.
- Desconexión externa: reintento limitado sólo dentro de la conversación vigente; al
  agotarse, cierre `failed`.
- Desconexión del dispositivo: cancelar proveedor, cerrar conversación como `abandoned` y
  limpiar colas.
- Error de cámara: devolver resultado de herramienta fallido y continuar conversación.
- Timeout o límite de duración: despedida si es posible, cierre durable y limpieza acotada.

Toda ruta inesperada emitirá un código público estable y un `correlation_id`.

El contrato contempla como mínimo estos códigos estables:

- `AI_UNCONFIGURED` y `AI_UNAVAILABLE`;
- `CONVERSATION_ALREADY_ACTIVE` y `CONVERSATION_NOT_ACTIVE`;
- `INVALID_AUDIO_FRAME`, `STREAM_NOT_FOUND`, `STREAM_NOT_OWNED` e `INVALID_SEQUENCE`;
- `AUDIO_INPUT_OVERFLOW` y `AUDIO_OUTPUT_OVERFLOW`;
- `CONVERSATION_TIMEOUT` y `CONVERSATION_IDLE_TIMEOUT`;
- `DEVICE_DISCONNECTED`.

Ningún error público expondrá stack traces ni mensajes internos del proveedor.

## 15. Interfaz administrativa de conversaciones

La sección `Conversaciones` mostrará:

- fecha y duración;
- dispositivo por nombre e identificador público;
- visitante y motivo cuando estén disponibles;
- resultado localizado;
- indicador de captura.

El detalle mostrará transcripción cronológica, resumen y captura autorizada. UUID, IDs de
comandos y metadatos técnicos quedarán en un bloque secundario expandible, no en la lectura
principal.

## 16. Correcciones de aceptación visual

Estas correcciones se implementan al final de SIM-2, una vez estable la conversación de
voz, la cámara, las herramientas y la administración avanzada.

1. **Cronología:** usar una grilla con columna flexible para etiqueta y columna estable para
   fecha; permitir wrap sin solapamiento en desktop y apilar en móvil.
2. **Eventos:** enriquecer la respuesta o resolver contra el catálogo del hogar para mostrar
   `Nombre · PI-000001`; el UUID sólo aparecerá en detalle técnico.
3. **Estadísticas:** conservar una sola leyenda accesible y eliminar la leyenda duplicada de
   Recharts.
4. **Diagnóstico:** mapear claves y valores conocidos a español, conservar valores
   desconocidos escapados y claramente técnicos.
5. **Auditoría:** mostrar nombre de usuario y copia pública localizada; IDs y contexto crudo
   sanitizado quedarán en detalle técnico.

Las correcciones conservarán el tema claro aprobado y no adoptarán la paleta oscura del
archivo generado `design-system/.../MASTER.md`.

## 17. Lifecycle y cleanup

Cada dispositivo puede tener como máximo una conversación activa, ligada simultáneamente
a su identidad y al socket autenticado que la abrió. Un segundo inicio devuelve
`CONVERSATION_ALREADY_ACTIVE`. Si desaparece ese socket, la conversación termina como
`abandoned`; una reconexión de presencia no revive la conversación cerrada.

La duración máxima es 300 segundos y la inactividad máxima 45 segundos. Toda salida —fin
normal, desconexión, timeout, overflow o error externo— cierra la sesión OpenAI, cancela y
espera las tareas async, detiene timers, vacía colas y buffers y elimina las referencias de
stream y conversación. No quedan tareas asyncio huérfanas.

## 18. Pruebas de SIM-1

### 18.1 Backend

- round-trip del header binario;
- rechazo de magic, versión, tipo, stream, longitud, payload o tamaño inválidos;
- rechazo de secuencia duplicada o fuera de orden;
- rechazo de binario antes de autenticación o sin `audio_pcm16_v1`;
- stream ownership y una conversación activa por dispositivo;
- overflow de entrada y salida, incluyendo cancelación, limpieza local y códigos estables;
- lifecycle completo usando `FakeRealtimeProvider`;
- VAD, barge-in, cancelación y envío de `conversation.audio.clear`;
- transcripciones finales sin duplicados;
- desconexión, reemplazo de socket, deshabilitación, rotación, timeout e idle timeout;
- OpenAI no configurado o no disponible;
- cleanup de sesiones, tasks, timers, colas y streams;
- logs y errores sin secretos, API keys ni audio.

### 18.2 Simulador web

- challenge/HMAC con Web Crypto y secreto sólo en memoria;
- conexión, desconexión y reconexión de presencia;
- permisos de micrófono denegados y dispositivo de audio ausente;
- AudioWorklet, resampling, PCM16 mono 24 kHz y frames de 20 ms;
- reproducción manos libres y cola acotada;
- recepción de `conversation.audio.clear` y vaciado efectivo del buffer local;
- estados UI, nivel de micrófono, transcripciones y finalización;
- reconexión sin revivir una conversación cerrada;
- accesibilidad por teclado, foco, regiones vivas y layout responsive.

## 19. Smoke test OpenAI opt-in

Las llamadas reales no forman parte de la suite normal. Un comando separado sólo se
ejecuta con `OPENAI_REALTIME_SMOKE=true` y comprueba:

1. apertura de una sesión breve;
2. envío acotado de audio sintético;
3. recepción de al menos un evento válido;
4. cierre y cleanup;
5. ausencia de secretos en la salida.

El comando falla de forma segura y nunca imprime la API key ni audio. No se ejecutará en
CI ni desde `scripts/verify.ps1` sin activación explícita.

## 20. Criterios de aceptación de SIM-1

SIM-1 queda aprobado sólo cuando:

1. un navegador limpio se autentica como dispositivo con el challenge/HMAC existente;
2. el secreto nunca se persiste ni aparece en logs, URLs o telemetría;
3. un inicio que abre Realtime crea exactamente una conversación durable, mientras que
   `AI_UNCONFIGURED` y `AI_UNAVAILABLE` no crean ninguna;
4. el micrófono viaja simulador -> backend -> OpenAI Realtime;
5. el audio del agente viaja OpenAI Realtime -> backend -> simulador;
6. la conversación funciona manos libres con VAD;
7. el visitante puede interrumpir al agente;
8. la interrupción cancela OpenAI y limpia la cola remota y el buffer local;
9. las transcripciones finales de visitante y agente aparecen en pantalla;
10. esas transcripciones quedan persistidas una sola vez;
11. no se almacena audio crudo;
12. existe como máximo una conversación activa por dispositivo;
13. desconexión, timeout, idle timeout y overflow de cualquier cola cierran y limpian
    correctamente;
14. `FakeRealtimeProvider` permite pruebas deterministas sin red;
15. el agente no posee herramienta ni afirmación de apertura de puerta;
16. todas las suites existentes continúan pasando;
17. `scripts/verify.ps1` continúa siendo válido.

La prueba manual obligatoria usa `PI-000001`, inicia con `Tocar timbre`, permite decir
“Hola, soy Juan, vengo a entregar un paquete”, escuchar la respuesta, interrumpirla y
finalizar la visita verificando que la conversación y la transcripción quedaron guardadas.

## 21. Pruebas y aceptación de SIM-2

SIM-2 agrega pruebas de webcam, patrón explícito, fallos sin fallback silencioso, timeout
de captura, media durable, herramientas con argumentos inválidos y repetidos, idempotencia
de `record_visit`, tenancy, RBAC, ocultamiento de media sin `camera.view`, listado/detalle
administrativo y las cinco correcciones visuales en desktop y móvil.

SIM-2 queda aprobado cuando:

1. `capture_visitor_image` produce una captura real o un fallo público explícito;
2. el patrón de prueba queda marcado como `source=test_pattern`;
3. `record_visit` deja un resultado durable e idempotente;
4. la conversación continúa ante un fallo recuperable de cámara;
5. el panel muestra listado, detalle y captura sólo con los permisos correctos;
6. dos hogares o dispositivos no pueden observar ni controlar datos ajenos;
7. las cinco correcciones visuales quedan verificadas;
8. la verificación completa del repositorio permanece verde.

## 22. Configuración nueva

```env
OPENAI_API_KEY=
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
CONVERSATION_MAX_SECONDS=300
CONVERSATION_IDLE_SECONDS=45
AUDIO_INPUT_QUEUE_FRAMES=50
AUDIO_OUTPUT_QUEUE_FRAMES=100
OPENAI_REALTIME_SMOKE=false
```

La API key sólo existe en backend: nunca se envía al frontend o dispositivo, no se guarda
en base de datos y no se imprime. El procedimiento local la solicitará como `SecureString`
y la escribirá en el archivo ignorado por Git sin mostrar su valor.

## 23. Orden de implementación obligatorio

La secuencia es:

1. inspección del repositorio y plan técnico basado en paths reales;
2. contrato JSON y binario de SIM-1;
3. parser binario;
4. `AIRealtimeProvider` y `FakeRealtimeProvider`;
5. coordinador y persistencia mínima;
6. integración de audio con el WebSocket existente;
7. simulador frontend, AudioWorklet y reproducción;
8. VAD, barge-in y `conversation.audio.clear`;
9. pruebas y verificación completa de SIM-1;
10. aceptación manual de SIM-1;
11. cámara y herramientas de SIM-2;
12. persistencia extendida y administración;
13. pruebas de SIM-2 y correcciones visuales;
14. runbook, documentación y verificación final.

No se adelantan cámara, herramientas ni administración avanzada antes de aprobar la voz
extremo a extremo.

## 24. Verificación y documentación final

Después de cada bloque se ejecutan las pruebas relevantes. Al cerrar cada hito se ejecutan
tests backend, tests frontend, lint, typecheck, build de producción y
`scripts/verify.ps1`. No se debilitan pruebas existentes para admitir el cambio.

La documentación final cubrirá arquitectura Realtime, protocolo JSON, header binario
`PAUD`, capacidades, configuración, autenticación, lifecycle, barge-in,
`conversation.audio.clear`, cámara, herramientas, límites, errores, smoke test y ejecución
del simulador. Se agregarán diagramas de flujo simples donde aclaren el recorrido.

## 25. Entregables

### SIM-1

- contrato JSON y binario documentado;
- proveedor OpenAI Realtime real y falso;
- coordinador y persistencia mínima de conversaciones;
- simulador web independiente con audio manos libres;
- smoke test externo opt-in;
- pruebas, aceptación manual y runbook de voz.

### SIM-2

- cámara real y patrón de prueba explícito;
- herramientas de captura y registro de visita;
- persistencia extendida y sección administrativa;
- correcciones visuales del MVP1;
- pruebas, runbook final y actualización de arquitectura.
