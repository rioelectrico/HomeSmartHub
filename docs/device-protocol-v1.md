# Protocolo de dispositivo V1

Este documento describe el contrato activo implementado por FastAPI, el simulador Python,
el simulador web SIM-1 y la base de firmware. El transporte es WebSocket en `/ws/device`
(`ws` en desarrollo, `wss` detrás de TLS). Autenticación, presencia, comandos y control de
conversación usan un objeto JSON por frame de texto codificado como UTF-8. Un dispositivo
que anuncie `audio_pcm16_v1` también puede enviar y recibir frames binarios `PAUD`. JSON
inválido, campos adicionales o un tipo no documentado se rechazan.

El navegador nunca se conecta directamente a OpenAI. El simulador web se comporta como
un dispositivo: navegador -> `/ws/device` -> FastAPI -> OpenAI Realtime, y el regreso sigue
el camino inverso. `OPENAI_API_KEY` existe sólo en el backend.

## Límites y reglas comunes

- `device.hello.version` vale exactamente `v1`; los controles `conversation.*` agregados
  por SIM-1 usan `version: 1` numérico.
- `device_id` cumple `^PI-[0-9]{6}$`.
- `boot_id` tiene entre 1 y 128 caracteres ASCII alfanuméricos, `_` o `-`; el dispositivo
  genera uno nuevo en cada arranque y lo conserva durante sus reconexiones.
- La secuencia JSON del dispositivo es un entero estricto entre `0` y
  `9223372036854775807`. `auth.response.seq` debe ser mayor que el de `device.hello` y,
  después de autenticar, cada mensaje debe tener `boot_id` idéntico y `seq` estrictamente
  mayor al último recibido. Una secuencia repetida o fuera de orden cierra la conexión.
  Los controles de conversación del servidor tienen su propia secuencia JSON creciente;
  los mensajes heredados `auth.*` y `command.request` no la llevan.
- Un mensaje WebSocket no puede superar `MAX_WEBSOCKET_MESSAGE_BYTES` bytes en UTF-8;
  el valor predeterminado es `262144`.
- Los timestamps son ISO 8601 con zona horaria. Los UUID se serializan con guiones.
- Los objetos variables `payload` y `result` tienen un máximo de 8192 bytes al
  serializarlos como JSON UTF-8 determinista. Los esquemas rechazan campos adicionales.

Los ejemplos siguientes son completos y usan identificadores, nonces, digest y tokens
ficticios pero sintácticamente válidos.

## Autenticación HMAC challenge-response

### 1. `device.hello` — dispositivo → backend

```json
{
  "type": "device.hello",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 0,
  "version": "v1",
  "device_id": "PI-000001",
  "capabilities": ["audio_pcm16_v1"]
}
```

El backend verifica versión, existencia y que el dispositivo no esté deshabilitado. La
capacidad `audio_pcm16_v1` es opcional; clientes MVP1 que la omiten conservan heartbeat,
estado y comandos, pero no pueden iniciar una conversación ni enviar audio.

### 2. `auth.challenge` — backend → dispositivo

```json
{
  "type": "auth.challenge",
  "algorithm": "HMAC-SHA256",
  "nonce": "example_nonce_0123456789ABCDEFGHijklmnop",
  "expires_at": "2026-09-05T15:00:15+00:00"
}
```

El challenge implementado expira a los 15 segundos, queda ligado al dispositivo y al
`boot_id`, y es de un solo uso. La verificación consume atómicamente un challenge elegible
incluso cuando el digest es incorrecto; nunca se puede reintentar el mismo nonce. Un
challenge vencido, consumido o perteneciente a otro arranque produce el mismo error opaco
`AUTH_FAILED`.

Rotar el secreto o deshabilitar mediante la API administrativa revoca también el
socket vigente: sólo después del commit exitoso se invalida su lease y token de upload,
se cierra con `4002 AUTH_FAILED` y se termina su conversación como `abandoned`. No se
espera otro heartbeat del dispositivo. La autenticación iniciada con la credencial
anterior no puede publicar una conexión después de la revocación; el sucesor debe
autenticar con la credencial vigente y no hereda la conversación anterior. Un rollback
no revoca la conexión.

### 3. Canonicalización y `auth.response`

Los bytes firmados son exactamente:

```text
v1\n{device_id}\n{boot_id}\n{nonce}
```

Cada `\n` representa un único byte LF (`0x0a`), no los dos caracteres barra y `n`. No hay
espacios, comillas, JSON, CR (`0x0d`) ni salto final. El dispositivo construye esa cadena,
la codifica como UTF-8 y calcula HMAC-SHA256 usando como clave los bytes UTF-8 del secreto.
Envía los 32 bytes del digest como 64 caracteres de hexadecimal minúsculo. El backend usa
comparación de tiempo constante.

Pseudocódigo normativo:

```text
canonical = UTF8("v1\n" + device_id + "\n" + boot_id + "\n" + nonce)
key       = UTF8(device_secret)
digest    = lowercase_hex(HMAC_SHA256(key, canonical))
```

```json
{
  "type": "auth.response",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 1,
  "nonce": "example_nonce_0123456789ABCDEFGHijklmnop",
  "digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

### 4. `auth.ok` — backend → dispositivo

```json
{
  "type": "auth.ok",
  "upload_token": "example-upload-token-not-valid-outside-this-document",
  "upload_expires_at": "2026-09-05T15:05:00+00:00",
  "heartbeat_interval_seconds": 15
}
```

El token de upload sólo vive en memoria, pertenece a la conexión vigente y expira a los
300 segundos. No es una credencial permanente y no debe registrarse ni persistirse. El
simulador vuelve a autenticar antes de su vencimiento.

## Presencia, heartbeat y estado

Después de `auth.ok`, el dispositivo envía una instantánea `device.status`. Luego envía
`device.heartbeat` con el intervalo indicado por el servidor.

```json
{
  "type": "device.status",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 2,
  "firmware_version": "simulator-1.0.0",
  "hardware_model": "PORTERO-SIMULATOR-V1",
  "uptime_seconds": 3,
  "ethernet": "online",
  "camera": "ready",
  "microphone": "unavailable",
  "speaker": "unavailable",
  "free_heap_bytes": 262144
}
```

`ethernet` admite `online`, `offline` o `error`; cámara, micrófono y parlante admiten
`ready`, `unavailable` o `error`.

```json
{
  "type": "device.heartbeat",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 3,
  "uptime_seconds": 18
}
```

Los heartbeats actualizan `last_seen_at`; no crean una fila por latido. Una desconexión
detectada marca OFFLINE inmediatamente. Si el transporte queda abierto pero deja de enviar,
la tarea de mantenimiento marca OFFLINE una sola vez después de
`DEVICE_OFFLINE_TIMEOUT` (45 segundos por defecto). Una autenticación o actividad válida
posterior marca ONLINE y crea una única transición nueva.

## Comandos

V1 implementa `camera.capture` y `device.status.request`. `access.unlock` permanece fuera
de alcance. La voz SIM-1 no es un comando: usa los controles `conversation.*` y frames
binarios definidos a continuación.

### Solicitud — backend → dispositivo

```json
{
  "type": "command.request",
  "command_id": "123e4567-e89b-12d3-a456-426614174000",
  "command": "camera.capture",
  "payload": {}
}
```

Los mensajes del backend no llevan `boot_id` ni `seq`. El dispositivo valida el comando,
responde primero `command.ack` y luego, si fue aceptado, `command.result`.

El simulador web SIM-1 procesa estos controles también durante una visita, sin cambiar
el estado ni la secuencia de conversación. Para `device.status.request` responde con
ACK `accepted`, una instantánea `device.status` y RESULT `completed` con `result: {}`;
los tres mensajes consumen la secuencia JSON saliente del dispositivo. Para
`camera.capture`, cuya cámara todavía no está implementada, responde únicamente con
ACK `rejected` y `error_code: "CAMERA_NOT_READY"`. No solicita acceso a la cámara ni
interrumpe voz o heartbeat. Tipos, comandos o envelopes no admitidos se rechazan con
`PROTOCOL_ERROR`; el payload de un comando válido conserva el límite de 8192 bytes.

### ACK aceptado — dispositivo → backend

```json
{
  "type": "command.ack",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 4,
  "command_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "accepted",
  "error_code": null
}
```

En un ACK `accepted`, `error_code` debe ser nulo u omitirse. En un ACK `rejected`, debe
estar presente y contener un código permitido.

### Resultado completado — dispositivo → backend

Después de una captura, el simulador sube primero el JPEG y usa los metadatos seguros de
la respuesta como `result`:

```json
{
  "type": "command.result",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 5,
  "command_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "completed",
  "result": {
    "id": "223e4567-e89b-12d3-a456-426614174001",
    "home_id": "323e4567-e89b-12d3-a456-426614174002",
    "device_id": "423e4567-e89b-12d3-a456-426614174003",
    "command_id": "123e4567-e89b-12d3-a456-426614174000",
    "content_type": "image/jpeg",
    "size_bytes": 633,
    "url": "/api/homes/323e4567-e89b-12d3-a456-426614174002/media/223e4567-e89b-12d3-a456-426614174001"
  },
  "error_code": null
}
```

Un resultado `completed` prohíbe `error_code`. Un resultado `failed` lo exige; `result`
puede quedar vacío:

```json
{
  "type": "command.result",
  "boot_id": "550e8400-e29b-41d4-a716-446655440000",
  "seq": 5,
  "command_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "failed",
  "result": {},
  "error_code": "CAMERA_CAPTURE_FAILED"
}
```

### Ciclo de vida durable

Sólo se permiten estas transiciones:

```text
pending -> sent -> acknowledged -> completed
pending -> failed
sent -> failed | timeout
acknowledged -> failed | timeout | completed
```

Una entrega de transporte fallida lleva `pending` a `failed`. Un ACK rechazado lleva
`sent` a `failed`. `sent` y `acknowledged` vencen según `COMMAND_TIMEOUT_SECONDS` (30 s
por defecto). Un ACK aceptado idéntico y un RESULT completado idéntico pueden repetirse de
forma idempotente; respuestas desconocidas, reordenadas o terminales diferentes se
rechazan y cierran el socket con error de protocolo.

Los códigos de dispositivo V1 son:

- `AUTH_FAILED`
- `PROTOCOL_VERSION_UNSUPPORTED`
- `INVALID_COMMAND`
- `INVALID_PARAMETER`
- `DEVICE_BUSY`
- `CAMERA_NOT_READY`
- `CAMERA_CAPTURE_FAILED`
- `MIC_NOT_READY`
- `SPEAKER_NOT_READY`
- `INTERNAL_ERROR`

## Upload JPEG

El upload no usa WebSocket ni Base64. Se envían los bytes JPEG crudos:

```http
POST /api/device/media?command_id=123e4567-e89b-12d3-a456-426614174000 HTTP/1.1
Authorization: DeviceUpload example-token-redacted
Content-Type: image/jpeg
Content-Length: 633

<633 bytes JPEG>
```

- `command_id` debe existir, pertenecer a `camera.capture` y estar `acknowledged` o
  `completed`.
- La cabecera de autorización usa exactamente el esquema `DeviceUpload` y el token
  efímero de la conexión actual.
- Sólo se acepta `Content-Type: image/jpeg` exacto.
- `MAX_UPLOAD_BYTES` vale `5242880` (5 MiB) por defecto. Se valida `Content-Length` si
  existe y se vuelve a imponer el límite durante el streaming.
- El contenido debe comenzar con SOI `FF D8` y terminar con EOI `FF D9`.
- El backend genera ruta y nombre, escribe a un temporal y publica atómicamente bajo
  `MEDIA_ROOT`. No acepta paths del dispositivo.
- La respuesta `201` contiene sólo IDs, tipo, tamaño y URL protegida. Abrir esa URL exige
  cookie web y permiso `camera.view` de la vivienda.

Errores HTTP estables: `400 INVALID_MEDIA` si `Content-Length` no es un entero o es
negativo; `401 AUTH_FAILED`; `404 INVALID_CORRELATION`; `409 INVALID_CORRELATION`;
`413 MEDIA_TOO_LARGE`; y `415 INVALID_MEDIA` para tipo MIME o marcadores JPEG inválidos.

## Cierre WebSocket

| Código | Razón | Causa típica |
|---:|---|---|
| código 1001 | `shutdown` | apagado normal del proceso backend |
| código 4000 | `PROTOCOL_ERROR` | JSON/tipo/campos inválidos o respuesta de comando fuera de orden |
| código 4001 | `replaced` | una conexión autenticada más nueva reemplazó esta sesión del dispositivo |
| código 4002 | `AUTH_FAILED` | identidad, credencial, challenge o dispositivo deshabilitado |
| código 4003 | `PROTOCOL_VERSION_UNSUPPORTED` | `device.hello.version` distinto de `v1` |
| código 4004 | `MESSAGE_TOO_LARGE` | frame mayor a `MAX_WEBSOCKET_MESSAGE_BYTES` |
| código 4005 | `SEQUENCE_INVALID` | `boot_id` diferente o `seq` no creciente |
| código 4006 | `HANDSHAKE_TIMEOUT` | hello o respuesta HMAC tarda más de 10 s |
| código 4500 | `INTERNAL_ERROR` | error interno no expuesto al dispositivo |

En forma abreviada, los cierres de ciclo de vida implementados son `4001 / replaced` y
`1001 / shutdown`; sus razones son literales y están en minúsculas.

## Reconexión

El simulador reconecta indefinidamente. Sin jitter, las bases son `1, 2, 4, 8, 15`
segundos y luego `DEVICE_RECONNECT_MAX_SECONDS` (30 s por defecto) en cada intento. Con
`DEVICE_RECONNECT_JITTER=true` —valor predeterminado— espera un valor aleatorio uniforme
entre cero y esa base para evitar reconexiones sincronizadas. Una sesión que finaliza
normalmente reinicia la serie. Cada reconexión conserva el `boot_id` del proceso pero abre
un challenge nuevo y reinicia la autenticación; `seq` sigue siendo creciente durante ese
arranque.

## Conversación de voz SIM-1

### Controles JSON

Después de `auth.ok`, sólo un cliente que anunció `audio_pcm16_v1` puede iniciar voz. El
dispositivo envía controles con su `boot_id` y su `seq` JSON creciente:

```json
{"type":"conversation.start","boot_id":"550e8400-e29b-41d4-a716-446655440000","seq":6,"version":1,"mode":"hands_free"}
{"type":"conversation.stop","boot_id":"550e8400-e29b-41d4-a716-446655440000","seq":7,"version":1,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","reason":"visitor_finished"}
{"type":"conversation.audio.output_overflow","boot_id":"550e8400-e29b-41d4-a716-446655440000","seq":8,"version":1,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","stream_id":"223e4567-e89b-12d3-a456-426614174001"}
```

El dispositivo no puede elegir hogar, prompt, instrucciones, modelo, voz ni herramientas.
El backend resuelve todo desde la identidad autenticada y `AgentConfig`. El modelo es
configurable; `env-default` resuelve `OPENAI_REALTIME_MODEL`, cuyo valor predeterminado es
`gpt-realtime-2.1`. SIM-1 no declara herramientas ni capacidad de abrir la puerta.

El servidor responde con controles de versión numérica `1` y su propia secuencia:

```json
{"type":"conversation.started","version":1,"seq":1,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","stream_id":"223e4567-e89b-12d3-a456-426614174001","audio":{"codec":"pcm16","sample_rate":24000,"channels":1,"frame_ms":20},"max_seconds":300}
{"type":"conversation.state","version":1,"seq":2,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","state":"listening"}
{"type":"conversation.transcript","version":1,"seq":3,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","role":"assistant","text":"Buen día.","final":true}
{"type":"conversation.audio.clear","version":1,"seq":4,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","stream_id":"223e4567-e89b-12d3-a456-426614174001","reason":"barge_in"}
{"type":"conversation.ended","version":1,"seq":5,"conversation_id":"123e4567-e89b-12d3-a456-426614174000","outcome":"registered","reason":"visitor_finished"}
{"type":"conversation.error","version":1,"seq":6,"code":"AI_UNAVAILABLE","correlation_id":"323e4567-e89b-12d3-a456-426614174002"}
```

Estados permitidos: `preparing`, `listening`, `visitor_speaking`, `assistant_speaking` y
`error`. Sólo se envían transcripciones finales; cada ítem del proveedor se deduplica antes
de persistirse. Nunca se persiste audio ni el payload externo crudo.

### Identidades distintas

`conversation_id es durable`: identifica el registro lógico en PostgreSQL durante toda la
visita. `stream_id es efímero`: identifica exclusivamente una instancia de transporte de
audio. Nunca se intercambian ni tienen el mismo valor. SIM-1 crea un stream por
conversación; un stream futuro podría reiniciarse sin cambiar la conversación durable.

### Header binario `PAUD`

Cada frame usa exactamente 34 bytes de header big-endian, seguidos por PCM16 little-endian:

```text
magic[4] | version:u8 | type:u8 | stream_id:uuid[16] | seq:u64be |
payload_length:u32be | payload[payload_length]
```

- `magic`: ASCII `PAUD`.
- `version`: `1`.
- `type=1`: dispositivo -> servidor; `type=2`: servidor -> dispositivo.
- `stream_id`: UUID efímero anunciado en `conversation.started`.
- `seq`: entero `u64` desde 1, estrictamente creciente por stream y dirección; no comparte
  la secuencia JSON.
- `payload_length`: cantidad exacta de bytes restantes.
- Payload nominal: 960 bytes, 480 muestras, 20 ms, PCM16 little-endian mono a 24 kHz.

El backend valida el tamaño total antes de decodificar y rechaza magic, versión, tipo,
longitud, payload vacío/impar/sobredimensionado, stream desconocido/ajeno y secuencia no
creciente. Binario antes de autenticar o sin `audio_pcm16_v1` también se rechaza.

### Backpressure, barge-in y cierre

La cola de entrada admite 50 frames nominales; la salida, 100. Un overflow cierra sólo la
conversación, no el WebSocket del dispositivo:

- `AUDIO_INPUT_OVERFLOW`: cancela la respuesta, limpia audio y termina la conversación.
- `AUDIO_OUTPUT_OVERFLOW`: cancela la respuesta activa, vacía la cola, envía
  `conversation.audio.clear` y termina la conversación para no acumular latencia.

La salida del navegador también está acotada a dos segundos, incluyendo las muestras
en tránsito hacia el AudioWorklet. Nunca elimina las muestras más antiguas para admitir
audio nuevo. Si una entrega excedería la capacidad pendiente, el navegador borra playback,
apaga captura/reproducción y muestra `AUDIO_OUTPUT_OVERFLOW` una sola vez. Envía el control
`conversation.audio.output_overflow` con ambos UUID distintos; requiere autenticación,
capacidad `audio_pcm16_v1`, `boot_id` y secuencia JSON válidos, y el stream exacto de la
conversación del socket propietario. No acepta un código de error arbitrario.

El coordinador aplica la misma secuencia cancelar → vaciar → `conversation.audio.clear`
→ `conversation.error(AUDIO_OUTPUT_OVERFLOW)` → `conversation.ended` con resultado
`failed` y razón `audio_output_overflow`. La transacción de cierre conserva el código
en un evento `conversation_failed` cuyo payload contiene sólo `conversation_id` y `code`.
Reportes duplicados durante/después del cierre no repiten la terminación; IDs ajenos o
viejos nunca cierran una conversación sucesora. El cliente valida pero no reproduce
frames en tránsito durante el cierre y espera `conversation.ended` más la liberación del
audio local antes de permitir otra visita. El heartbeat y el WebSocket siguen activos.

El worklet confirma las muestras reproducidas o efectivamente vaciadas para liberar
capacidad; enviar `clear` todavía no libera los mensajes en tránsito. Los contadores
por generación impiden que una confirmación previa libere audio nuevo. Un worklet que detectó
saturación permanece silenciado hasta destruirse, incluso si llega un `clear` por barge-in.
Una nueva visita crea recursos de audio nuevos.

Cuando el VAD detecta voz del visitante durante una respuesta, el backend cancela la
respuesta en OpenAI, descarta la salida pendiente y envía
`conversation.audio.clear(reason="barge_in")`; el AudioWorklet borra inmediatamente su
buffer local. Duración máxima e inactividad son 300 y 45 segundos por defecto.

Los errores públicos son `AI_UNCONFIGURED`, `AI_UNAVAILABLE`,
`CONVERSATION_ALREADY_ACTIVE`, `CONVERSATION_NOT_ACTIVE`, `INVALID_AUDIO_FRAME`,
`AUDIO_INPUT_OVERFLOW`, `AUDIO_OUTPUT_OVERFLOW`, `STREAM_NOT_FOUND`, `STREAM_NOT_OWNED`,
`INVALID_SEQUENCE`, `CONVERSATION_TIMEOUT`, `CONVERSATION_IDLE_TIMEOUT` y
`DEVICE_DISCONNECTED`. Llevan un `correlation_id`, nunca stack traces ni detalle crudo del
proveedor. Una reconexión no revive una conversación cerrada.

## SIM-2 — no implementado

Cámara dentro de la conversación, herramientas OpenAI y administración avanzada de visitas
permanecen fuera de este contrato hasta la aprobación explícita de SIM-1.
