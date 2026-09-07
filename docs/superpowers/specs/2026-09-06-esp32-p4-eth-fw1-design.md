# ESP32-P4-ETH FW-1: diseño de red y autenticación

**Estado:** FW-1 aprobado para implementación física

**Fecha:** 2026-09-06

**Placa:** Waveshare ESP32-P4-ETH, SKU 32086

**SoC detectado:** ESP32-P4 revisión v1.3

**Puerto de desarrollo detectado:** COM3 (USB-Enhanced-SERIAL CH343)

## 1. Objetivo

Implementar el primer hito físico del Portero Inteligente sobre la placa
Waveshare ESP32-P4-ETH, reutilizando el contrato de dispositivo V1 ya validado
por el simulador y sin modificar el backend.

El resultado de FW-1 debe realizar de forma estable:

```text
ESP32-P4-ETH
  -> Ethernet + DHCP
  -> WebSocket /ws/device
  -> challenge/HMAC-SHA256
  -> device.status
  -> heartbeat
  -> ONLINE
```

También debe recuperarse automáticamente ante la caída del enlace, del socket
o del backend. Audio, PAUD, cámara y pulsador están fuera de FW-1.

## 2. Fuentes de verdad inspeccionadas

La implementación deberá derivarse de estas fuentes, en este orden:

1. Implementación efectiva del backend y sus pruebas.
2. Cliente web del simulador y simulador Python.
3. `docs/device-protocol-v1.md`.
4. Esquema y documentación oficial de Waveshare para ESP32-P4-ETH.
5. Ejemplos locales del fabricante en
   `C:\PorteroIA\ESP32-P4-Platform`, sin modificar ese repositorio.

Archivos locales principales:

- `backend/app/websocket/device.py`
- `backend/app/devices/challenges.py`
- `backend/app/schemas/devices.py`
- `backend/app/devices/presence.py`
- `backend/app/devices/connections.py`
- `frontend/lib/simulator/device-client.ts`
- `frontend/lib/simulator/contracts.ts`
- `tools/device_simulator/src/portero_simulator/`
- `firmware/components/device_protocol/`
- `firmware/components/hardware_hal/`
- `firmware/test_apps/device_protocol/`

Fuentes oficiales de la placa:

- <https://docs.waveshare.com/ESP32-P4-ETH>
- <https://docs.waveshare.com/ESP32-P4-ETH/Resources-And-Documents>
- <https://files.waveshare.com/wiki/ESP32-P4-ETH/ESP32-P4-ETH-datasheet.pdf>
- <https://docs.waveshare.com/ESP32-P4-ETH/ESP-IDF>

## 3. Estado actual reutilizable

El repositorio ya contiene una base ESP-IDF neutral en `firmware/`:

- máquina de conexión hasta el estado autenticado;
- `boot_id` validado;
- contador JSON monotónico;
- valor de backoff;
- primitivas de mensajes de control;
- HAL neutral;
- trece pruebas Unity para transiciones, secuencia y gramática de `boot_id`.

Todavía no existen:

- implementación física de Ethernet;
- NVS o consola de aprovisionamiento;
- cliente WebSocket;
- HMAC en firmware;
- serialización y parsing JSON completos;
- heartbeat real;
- pruebas de integración en la placa.

El entorno actual tampoco tiene ESP-IDF activo: `idf.py` no está disponible ni
existe `IDF_PATH`. No se declarará compilación ni prueba física exitosa hasta
instalar y activar la versión fijada.

## 4. Decisión de estructura

Se migrará la aplicación física a `firmware/esp32-p4` y se conservarán los
componentes reutilizables en el nivel común:

```text
firmware/
├── components/
│   ├── device_protocol/
│   └── hardware_hal/
├── test_apps/
│   └── device_protocol/
└── esp32-p4/
    ├── CMakeLists.txt
    ├── sdkconfig.defaults
    ├── partitions.csv
    ├── README.md
    ├── main/
    │   ├── app_main.c
    │   ├── app_state.c
    │   ├── device_config.c
    │   ├── network_manager.c
    │   ├── websocket_transport.c
    │   ├── device_auth.c
    │   ├── CMakeLists.txt
    │   └── idf_component.yml
    └── components/
        └── board_port/
            ├── board_port.c
            ├── include/board_port.h
            └── CMakeLists.txt
```

`firmware/esp32-p4/CMakeLists.txt` utilizará `EXTRA_COMPONENT_DIRS` para
referenciar los componentes comunes. La aplicación neutral actual se migrará;
no se copiará ni se mantendrán dos aplicaciones equivalentes.

Responsabilidades:

- `app_main.c`: inicialización y coordinación mínima.
- `app_state.c`: única fuente de verdad del estado de conexión.
- `device_config.c`: consola USB, validación y persistencia NVS.
- `network_manager.c`: Ethernet, eventos de enlace, DHCP e información IP.
- `websocket_transport.c`: ciclo de vida, frames y reconexión del WebSocket.
- `device_auth.c`: canonicalización y HMAC-SHA256.
- `board_port`: modelo de PHY y señales exclusivas de esta placa.
- `device_protocol`: reglas independientes del hardware y secuencia JSON.

El estado autenticado que la base denomina `IDLE` se consolidará como
`ONLINE`. No existirán dos máquinas de estados paralelas.

## 5. Plataforma fijada

Configuración inicial:

```text
ESP-IDF                 5.5.4
target                  esp32p4
silicon revision        v1.3 / perfil rev1_3
flash                   32 MB según placa
PSRAM                   32 MB según placa
PHY                     IP101GR
interfaz                 RMII
alimentación operativa   PoE mediante módulo oficial Waveshare
```

Se elige ESP-IDF 5.5.4 porque es la versión recomendada por la documentación
actual de la placa y satisface las dependencias del BSP que se necesitarán en
etapas posteriores. No se usarán binarios ni `sdkconfig` generados para
`rev3_x`, porque no son compatibles con el chip v1.3 detectado.

El perfil reproducible incluirá:

```text
CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y
CONFIG_ESP32P4_REV_MIN_100=y
CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
```

`REV_MIN_100` habilita la familia v1.x; la revisión física exacta v1.3 se
verificará otra vez durante el flash y el arranque.

La unidad de despliegue será la variante `ESP32-P4-POE-ETH` (SKU 32088), o la
placa base SKU 32086 con el módulo PoE oficial Waveshare correctamente montado.
La documentación del fabricante confirma que este conjunto transporta red y
alimentación por un único cable. No se utilizará un inyector PoE pasivo ni una
fuente fuera de las especificaciones del módulo. USB Type-C queda permitido para
flasheo y diagnóstico, pero las pruebas operativas indicadas como PoE-only se
ejecutarán sin alimentación USB.

### 5.1 Señales Ethernet verificadas

El esquema oficial de Waveshare define:

```text
MDC                 GPIO31
MDIO                GPIO52
PHY reset           GPIO51
RMII clock input    GPIO50
TX_EN               GPIO49
TXD0                GPIO34
TXD1                GPIO35
CRS_DV              GPIO28
RXD0                GPIO29
RXD1                GPIO30
PHY address         1
```

Estos valores vivirán únicamente en `board_port`. No se copiará el overlay de
CI `sdkconfig.ci.default_ip101` del ejemplo genérico, porque contiene GPIO de
otra configuración y contradice el esquema de esta placa.

## 6. Aprovisionamiento

Los campos requeridos para la operación normal son:

```text
device_id
device_secret
backend_url
```

El dispositivo soporta dos mecanismos de aprovisionamiento que comparten
exactamente la misma lógica de validación y persistencia NVS:

```text
                     +-------------------------------+
USB portero-config ->|  device_config_validate_*()   |
                     |  device_config_save()          |-> NVS dual-slot
Web POST /configure ->|  device_config_erase()        |
                     +-------------------------------+
```

### 6.1 Método principal: portal web por Ethernet

Cuando el ESP32 no tiene configuración válida, inicia Ethernet, obtiene una
dirección DHCP y levanta un servidor HTTP local. El operador abre un navegador
desde cualquier dispositivo de la misma LAN:

```text
http://<ip-asignada-por-DHCP>/
```

Opcionalmente puede habilitarse mDNS para acceder mediante:

```text
http://portero.local/
```

El soporte de mDNS no es obligatorio para completar FW-1; si añade complejidad
significativa se pospone a una revisión posterior.

La página contiene únicamente los tres campos necesarios y un botón de envío.
El formulario siempre se envía mediante `POST`; nunca mediante `GET` ni
query parameters. La plantilla HTML está embebida en el firmware como literal
de cadena; no depende de CDN, JavaScript complejo ni assets externos.

El servidor HTTP de aprovisionamiento:

- sólo arranca cuando el dispositivo está en estado `PROVISIONING`;
- se destruye y libera sus recursos al completar el aprovisionamiento o
  al salir de `PROVISIONING`;
- nunca queda activo cuando el dispositivo está en modo normal/ONLINE;
- no abre el WebSocket del backend mientras está activo.

### 6.2 Mecanismo secundario: consola USB

`portero-config` permanece disponible como mecanismo de recuperación,
diagnóstico y reprovisionamiento manual en caso de que el portal web no pueda
utilizarse. El operador ejecuta el comando; la consola pide los campos de forma
interactiva. La entrada del secreto no tiene eco ni se acepta como argumento.

### 6.3 Validaciones y seguridad compartidas

Validaciones mínimas de FW-1 aplicadas por ambos mecanismos:

- `device_id`: expresión `^PI-[0-9]{6}$`.
- `device_secret`: entre 32 y 128 caracteres según el contrato existente.
- `backend_url`: URL completa
  `ws://<host-o-ip>[:puerto]/ws/device`, sin credenciales, query ni fragmento.
- `localhost`, `127.0.0.0/8`, `[::1]`, paths distintos de `/ws/device` y URLs
  relativas se rechazan.
- `wss://` se rechaza explícitamente como no soportado en FW-1. Se habilitará
  sólo cuando exista validación de CA y hostname.

El secreto no aparecerá en código, `sdkconfig`, commits, logs, errores ni
comandos documentados. Para laboratorio se autoriza `ws://` dentro de la LAN.
La URL nunca será `localhost`, porque desde la placa eso identifica al propio
ESP32.

En FW-1 el secreto queda protegido lógicamente por las fronteras de software,
pero no está protegido contra extracción física de la flash porque NVS
Encryption, Flash Encryption y Secure Boot permanecen fuera de alcance. Este
riesgo se acepta exclusivamente para laboratorio y prototipo; esta persistencia
no representa la seguridad final del producto.

Los tres valores se escriben en una transacción lógica con marcador de
versión/validez; una interrupción no debe dejar una mezcla de configuración
nueva y antigua. Los buffers que hayan contenido el secreto se borrarán con
`mbedtls_platform_zeroize()`. Una actualización válida reiniciará el
dispositivo.

## 6B. Portal web de aprovisionamiento por Ethernet

### 6B.1 Arquitectura

El portal utiliza `esp_http_server`, componente incluido en ESP-IDF sin
dependencias externas adicionales. Su interfaz pública es mínima y desacoplada:

```c
esp_err_t provisioning_web_start(void);
esp_err_t provisioning_web_stop(void);
bool      provisioning_web_is_running(void);
```

`app_supervisor` es el único responsable de llamar a `provisioning_web_start()`
al entrar en `PROVISIONING` con IP válida, y `provisioning_web_stop()` al
salir. No existe un administrador de Ethernet separado para el modo de
aprovisionamiento: el mismo `network_manager` sirve a ambos modos.

### 6B.2 Separación estricta de modos

| Recurso | PROVISIONING | NORMAL |
|---|---|---|
| Ethernet | Sí | Sí |
| DHCP | Sí | Sí |
| Servidor HTTP config | Sí | No |
| WebSocket backend | No | Sí |
| HMAC challenge | No | Sí |
| Heartbeat | No | Sí |

### 6B.3 Flujo de aprovisionamiento web

```text
ESP32 sin configuración válida
  -> PROVISIONING
  -> network_manager_start()
  -> DHCP adquirido
  -> provisioning_web_start()
  -> [APP] IP: <ip>  URL: http://<ip>/
  -> usuario abre navegador, carga GET /
  -> usuario completa formulario y envía POST /configure
  -> validar device_id, backend_url, device_secret
  -> device_config_save() — escritura atómica NVS
  -> zeroizar buffers del secreto
  -> responder 200 OK con página de éxito
  -> provisioning_web_stop()
  -> esp_restart()
  -> BOOT con NVS válida
  -> modo normal: Ethernet -> WebSocket -> HMAC -> ONLINE
```

Si la validación falla, el servidor responde con el error específico y permanece
activo. El dispositivo no reinicia. El WebSocket del backend no se abre.

### 6B.4 Endpoints mínimos

**`GET /`**

Devuelve HTML embebido en firmware. Autocontenido, sin dependencias externas.
Contiene exactamente los campos `device_id`, `backend_url` y `device_secret`
con un único botón de envío.

**`POST /configure`**

1. Verifica `Content-Type: application/x-www-form-urlencoded`.
2. Limita el body a `PROVISIONING_WEB_MAX_BODY_BYTES` (≤ 1 KiB).
3. Parsea exactamente los tres campos; descarta campos no reconocidos.
4. Valida usando `device_config_validate_device_id()`,
   `device_config_validate_secret()` y `device_config_validate_backend_url()`.
5. Persiste mediante `device_config_save()`.
6. Nunca registra el secreto.
7. Zeroiza con `mbedtls_platform_zeroize()` el buffer que contuvo el secreto.
8. Responde `200 OK` con página de confirmación.
9. Llama a `provisioning_web_stop()` y `esp_restart()`.
10. Si falla la persistencia, responde error y permanece activo; no reinicia.

### 6B.5 Seguridad del portal FW-1

**Reglas obligatorias:**

- El secret nunca aparece en respuestas de error, logs, URLs ni en el HTML.
- Nunca se usa `GET` para guardar configuración.
- Nunca se usa query parameter para el secret.
- El HTML del formulario no pre-rellena el campo secret con valor existente.
- El servidor está disponible únicamente durante `PROVISIONING`.
- El body está limitado a ≤ 1 KiB antes de copiar cualquier byte.
- Múltiples POST concurrentes no pueden producir dos escrituras: el handler
  usa un mutex o verifica el estado de `PROVISIONING` al inicio.

**Nota de seguridad de FW-1:** el portal usa HTTP sin TLS porque opera dentro
de una LAN de laboratorio y no existe una autoridad de certificación interna
en FW-1. Este modelo es aceptable únicamente para el prototipo en red privada.
Antes de una instalación en producción se requiere TLS con CA interna o
aprovisionamiento out-of-band. `wss://` y HTTPS no se implementan en FW-1.

## 7. Flujo de conexión

La máquina de estados mínima de FW-1 será:

```text
BOOT
  -> PROVISIONING, si falta configuración
       -> Ethernet + DHCP
       -> provisioning_web_start()  (HTTP portal)
       -> [esperar POST /configure]
       -> device_config_save() -> esp_restart()
  -> NETWORK_CONNECTING
  -> BACKEND_CONNECTING
  -> AUTHENTICATING
  -> ONLINE
  -> ERROR transitorio
  -> NETWORK_CONNECTING, durante recuperación
```

Una sola capa será dueña de las transiciones. Los callbacks de Ethernet y
WebSocket publicarán eventos y no mutarán múltiples subsistemas por separado.

**Bifurcación PROVISIONING:** si `device_config_load()` devuelve error al
arrancar, el supervisor entra en `PROVISIONING`, inicia `network_manager` para
obtener IP por DHCP y luego llama `provisioning_web_start()`. El WebSocket del
backend no se abre. El portal HTTP permanece activo hasta recibir un `POST
/configure` válido, tras el cual guarda la configuración y ejecuta
`esp_restart()`. No existe transición directa de `PROVISIONING` a
`NETWORK_CONNECTING` sin reinicio.

**Modo normal (NORMAL):** requiere configuración NVS válida en boot. El portal
HTTP no arranca. El WebSocket abre inmediatamente tras obtener IP.

Secuencia normal:

1. Inicializar `esp_netif` y el event loop.
2. Inicializar EMAC/IP101GR mediante `board_port`.
3. Esperar enlace y dirección DHCP.
4. Abrir la URL WebSocket configurada.
5. Completar el handshake en menos de diez segundos por fase.
6. Enviar inmediatamente `device.status` después de `auth.ok`.
7. Programar `device.heartbeat` con el intervalo informado por el servidor.

## 8. Contrato de autenticación

### 8.1 Hello

Primer mensaje del dispositivo:

```json
{
  "type": "device.hello",
  "boot_id": "identificador-del-arranque",
  "seq": 0,
  "version": "v1",
  "device_id": "PI-000001",
  "capabilities": []
}
```

FW-1 anunciará una lista vacía. `audio_pcm16_v1` se agregará solamente cuando
audio de entrada, salida y buffers estén implementados. El backend actualmente
no admite una capability `camera_jpeg_v1`.

### 8.2 Challenge y HMAC

El backend devuelve `auth.challenge` con algoritmo `HMAC-SHA256`, `nonce` y
vencimiento. El firmware construye exactamente:

```text
canonical = UTF8("v1\n" + device_id + "\n" + boot_id + "\n" + nonce)
digest    = lowercase_hex(HMAC_SHA256(UTF8(device_secret), canonical))
```

No se usa CRLF, JSON ni un salto final adicional. El challenge es de un solo
uso y no se reintenta el mismo nonce.

Vector compartido obligatorio:

```text
secret    = secret
device_id = PI-000001
boot_id   = boot-123
nonce     = nonce-456
digest    = ad78e27952573ad8aa6ea6d2593f5db5ee69fe1102aaaf6aca4016dc43210975
```

Este es un vector criptográfico unitario. Su nonce corto no es un mensaje wire
válido y no se reutilizará como fixture de integración; los tests del parser
usarán nonces que cumplan la longitud del esquema real.

### 8.3 Secuencias e identidad

- `boot_id` se genera una vez por arranque a partir de 128 bits obtenidos del
  RNG del ESP32 y se codifica como 32 caracteres hexadecimales minúsculos.
- La MAC, el timestamp y los contadores no se usarán como fuente única ni se
  incorporarán al identificador.
- El mismo `boot_id` sobrevive a todas las reconexiones de ese arranque y se
  reemplaza únicamente después de un reinicio real.
- Su gramática es `[A-Za-z0-9_-]`, con longitud de 1 a 128.
- `device.hello` usa exactamente `seq = 0`; el allocator existente emite
  después `auth.response` con `seq = 1` y continúa hasta `INT64_MAX`.
- `auth.response.seq` debe ser mayor que el de `device.hello`.
- Después de autenticar, todos los mensajes del dispositivo conservan
  `boot_id` y una secuencia estrictamente creciente.
- Los futuros contadores PAUD serán independientes.
- El tracking de secuencia para controles de conversación del servidor queda
  fuera de FW-1, porque `auth.*` y `command.request` no llevan esa secuencia.

## 9. Presencia y heartbeat

Después de `auth.ok`, el firmware enviará una instantánea `device.status` con
valores reales disponibles en FW-1. Cámara, micrófono y parlante se declararán
`unavailable` hasta sus respectivas etapas.

El heartbeat:

- usará `heartbeat_interval_seconds` recibido del backend;
- incluirá uptime real;
- no esperará ACK;
- no bloqueará la tarea de red;
- se cancelará antes de destruir o reemplazar el socket.

Los valores actuales del backend son 15 segundos para heartbeat y 45 segundos
para marcar una conexión inactiva como offline, pero el firmware no fijará el
intervalo del servidor en código.

### 9.1 Comandos administrativos durante FW-1

El backend puede enviar `command.request` aunque la lista de capabilities esté
vacía. El firmware responderá sin cerrar la conexión:

- `device.status.request`: `command.ack accepted`, nueva instantánea
  `device.status` y `command.result completed`.
- `camera.capture`: `command.ack rejected` con `CAMERA_NOT_READY`; no enviará
  resultado.
- Un `command.request` cuyo envelope sea válido pero cuyo nombre no esté
  soportado: `command.ack rejected` con `INVALID_COMMAND`, conservando el
  WebSocket. Se usa `INVALID_COMMAND` porque es el código compatible con el
  contrato V1 actual; `UNSUPPORTED_COMMAND` no forma parte del esquema.

Cada respuesta generada por el dispositivo consumirá la siguiente secuencia
JSON. JSON malformado, un envelope inválido o campos adicionales prohibidos se
tratarán como error de protocolo y cerrarán la conexión. Un nombre de comando
desconocido dentro de un `command.request` válido no cerrará la conexión.

## 10. WebSocket y concurrencia

El transporte soportará:

- frames de texto UTF-8;
- reensamblado explícito de mensajes fragmentados;
- máximo acumulado de 12.288 bytes por mensaje JSON recibido;
- desconexión, timeout y cierre remoto;
- propiedad inequívoca de cada instancia de socket.

El límite se aplicará durante el reensamblado, antes de ampliar o reservar el
buffer. Un mensaje que lo exceda provoca cierre local por error de protocolo.
Los frames binarios son inesperados en FW-1 y también provocan cierre y
handshake nuevo; PAUD se habilitará junto con `audio_pcm16_v1`.

Los callbacks publicarán eventos breves. No realizarán HMAC costoso, acceso NVS
ni esperas largas dentro del callback. Heartbeat y reconexión se apoyarán en
timers, event groups o colas de FreeRTOS, evitando `busy loops`.

## 11. Reconexión

Se reutilizará la política probada por el simulador Python:

```text
1, 2, 4, 8, 15, 30, 30... segundos
```

Cada espera incorporará jitter uniforme entre el 50 % y el 100 % del valor
base, con un mínimo absoluto de un segundo. Por lo tanto, ni siquiera un
`AUTH_FAILED` puede generar un reintento inmediato. La reconexión:

1. cancela heartbeat;
2. invalida la propiedad local del socket;
3. limpia cualquier estado efímero;
4. conserva `boot_id` y el contador JSON del arranque;
5. abre un WebSocket nuevo;
6. obtiene un challenge nuevo;
7. vuelve a autenticar;
8. retorna a `ONLINE` y publica status.

Un cierre `4001/replaced` invalida inmediatamente el socket anterior. Un
`4002/AUTH_FAILED` aplica backoff y produce un log opaco; no inicia un bucle
agresivo.

## 12. Errores, seguridad y logs

Tags previstos:

```text
[APP] [CFG] [ETH] [WS] [AUTH] [PROTO]
```

Se permite registrar estado, duración, códigos de cierre, intentos y uso de
memoria. Se prohíbe registrar:

- `device_secret`;
- cadena canónica completa;
- digest HMAC;
- `upload_token`;
- claves de API;
- mensajes que puedan contener credenciales.

Casos mínimos:

- configuración ausente: `PROVISIONING`;
- sin enlace o DHCP: retry acotado;
- handshake vencido: destruir socket y reconectar;
- error de protocolo: cierre y handshake nuevo;
- credenciales rechazadas: backoff;
- backend reiniciado: recuperación automática;
- cable desconectado: liberar transporte y esperar enlace.

`ws://` queda limitado al laboratorio. FW-1 rechazará `wss://` de forma
explícita. Una etapa posterior incorporará validación de CA y hostname antes de
habilitarlo; nunca se aceptarán certificados indiscriminadamente.

## 13. Dependencias previstas

Dependencias ESP-IDF que el plan deberá verificar contra 5.5.4:

- `esp_eth`;
- `esp_netif`;
- `esp_event`;
- `nvs_flash`;
- `esp_http_server` — portal web de aprovisionamiento (Task 6B); componente
  incluido en ESP-IDF sin dependencias externas adicionales;
- componente administrado `espressif/esp_websocket_client` fijado inicialmente
  a `1.8.0` en `main/idf_component.yml`, verificando su resolución con
  ESP-IDF 5.5.4 antes de consolidar el lockfile;
- mbedTLS para HMAC-SHA256;
- cJSON o parser incluido por ESP-IDF.

No se agregará el BSP de audio en FW-1. Tampoco se importarán carpetas enteras
del ejemplo del fabricante: sólo se adaptarán las partes necesarias y
compatibles con la licencia y la placa confirmada.

Sólo para FW-1, la tabla `partitions.csv` neutral existente se migrará sin
alterar su layout:
NVS en `0x9000` con tamaño `0x6000` y una aplicación factory en `0x10000` con
tamaño `0x1F0000`. Es suficiente para FW-1, conserva NVS y evita introducir OTA
fuera de alcance. La selección de flash será explícitamente de 32 MB aunque el
ejemplo Ethernet genérico del fabricante use 16 MB.

Esta tabla no es la arquitectura definitiva del producto. Antes de incorporar
OTA y al dimensionar cámara/audio se rediseñará y se volverán a validar tamaños,
slots, rollback y almacenamiento persistente.

## 14. Estrategia de pruebas

### 14.1 Pruebas automáticas

1. Migración estructural sin cambio semántico de la base neutral.
2. Validación de configuración, URL completa, reaprovisionamiento y rechazo de
   entradas inválidas.
3. Vector HMAC compartido con backend/simulador.
4. Serialización estricta de hello, auth, status y heartbeat.
5. Parsing estricto de challenge y auth.ok.
6. `device.hello seq=0`, `auth.response seq=1`, secuencia posterior monotónica
   y conservación de `boot_id`.
7. Backoff, jitter y cancelación de timers.
8. Transiciones ante link up/down y WebSocket open/close.
9. Fragmentación, límite incremental de 12.288 bytes y rechazo de binarios.
10. `device.status.request` aceptado y `camera.capture` rechazado sin romper el
    socket.
11. Comando desconocido rechazado con `INVALID_COMMAND` sin romper el socket.
12. Corte de alimentación durante actualización de NVS: al reiniciar debe
    conservarse la configuración anterior completa o la nueva completa, nunca
    una combinación.
13. Arranque con backend apagado: debe mantener backoff y alcanzar `ONLINE`
    cuando el backend aparezca, sin reiniciar la placa.
14. Pruebas Unity de componentes ESP-IDF.
15. Compilación completa para `esp32p4` con perfil v1.x sobre chip v1.3.
16. Suite backend y frontend existente para comprobar regresiones.
17. Escaneo de secretos y `git diff --check`.

**Pruebas adicionales para Task 6B (portal web de aprovisionamiento):**

18. `provisioning_web_start()` retorna `ESP_OK` en contexto normal.
19. `provisioning_web_is_running()` devuelve `true` tras `start()`, `false` tras `stop()`.
20. `GET /` devuelve `200 OK` con `Content-Type: text/html` y body no vacío.
21. El HTML de `GET /` contiene campos `device_id`, `backend_url` y `device_secret`.
22. `POST /configure` con los tres campos válidos devuelve `200 OK`.
23. `POST /configure` con `device_id` inválido devuelve `400` sin reiniciar.
24. `POST /configure` con `backend_url` inválida devuelve `400` sin reiniciar.
25. `POST /configure` con `device_secret` vacío devuelve `400` sin reiniciar.
26. `POST /configure` con body mayor que `PROVISIONING_WEB_MAX_BODY_BYTES` devuelve `413`.
27. `POST /configure` con campo extra adicional acepta o descarta silenciosamente
    los campos desconocidos sin error.
28. `GET /` no pre-rellena el campo `device_secret` con valor existente.
29. El servidor no registra el `device_secret` en ningún log Unity ni serial.
30. `provisioning_web_stop()` después de `start()` libera el handle; `is_running()`
    devuelve `false`.
31. Llamar a `stop()` sin `start()` previo no produce fallo ni assert.
32. Llamar a `start()` dos veces seguidas devuelve error sin doble-bind de puerto.
33. `POST /configure` no acepta `Content-Type: application/json` (responde `400`).
34. El HTML de `GET /` usa `method="post"` (case-insensitive).

### 14.3 Prueba física adicional para Task 6B

```text
borrar NVS
-> reiniciar placa
-> Ethernet + DHCP
-> serie muestra IP asignada y URL http://<ip>/
-> abrir navegador desde PC de la misma LAN
-> completar formulario con device_id, backend_url, device_secret
-> enviar
-> serie muestra "Configuración guardada. Reiniciando…"
-> placa reinicia
-> portal HTTP no arranca
-> placa conecta WebSocket -> HMAC -> ONLINE
```

### 14.2 Prueba física obligatoria

```text
encender placa
-> alimentación PoE-only estable
-> link Ethernet
-> DHCP
-> WebSocket
-> challenge/HMAC
-> ONLINE en Diagnóstico
-> status
-> heartbeat estable
-> reiniciar backend
-> reconexión automática
-> retirar y reponer cable
-> recuperación automática
```

Se registrará evidencia del monitor serial y del panel. La prueba de estabilidad
durará 30 minutos alimentada exclusivamente por PoE y con heartbeat continuo.
Antes se realizarán diez arranques en frío PoE-only, verificando en cada uno
boot, enlace, DHCP, autenticación y ausencia de reinicios por caída de tensión.
La evidencia identificará el modelo de PSE/switch o inyector y el módulo PoE,
sin registrar credenciales. Después de tres ciclos de
calentamiento se ejecutarán 25 reconexiones controladas; la diferencia entre la
mediana del heap libre de los primeros cinco y los últimos cinco ciclos no
podrá superar 4 KiB ni mostrar una tendencia descendente continua. Ningún
reintento podrá comenzar antes de un segundo.

## 15. Criterios de aceptación de FW-1

FW-1 queda aprobado cuando:

1. Compila con ESP-IDF 5.5.4 para ESP32-P4 v1.3.
2. La variante PoE aprobada completa diez arranques en frío PoE-only y obtiene
   enlace, DHCP, gateway y dirección IP sin reinicios por caída de tensión.
3. Se conecta al endpoint WebSocket actual sin cambios del backend.
4. HMAC coincide con el vector compartido y autentica credenciales reales.
5. El backend identifica el `device_id` correcto.
6. El dispositivo aparece online y publica status real.
7. Heartbeat mantiene presencia durante 30 minutos con alimentación PoE-only.
8. Reiniciar backend recupera la conexión automáticamente.
9. Retirar y reponer Ethernet recupera la conexión automáticamente.
10. Credenciales inválidas respetan un mínimo de un segundo entre intentos y
    el backoff definido.
11. Veinticinco reconexiones, después del calentamiento, cumplen el umbral de
    heap definido en la estrategia de pruebas.
12. Un corte durante reprovisionamiento conserva una versión completa y válida
    de la configuración.
13. La placa iniciada sin backend alcanza `ONLINE` cuando el servicio aparece,
    sin reiniciarse.
14. No se versionó ni registró ningún secreto.
15. El simulador web continúa autenticando y funcionando.
16. Todas las pruebas existentes siguen pasando.
17. (Task 6B) NVS borrada → reiniciar → DHCP → portal web accesible desde
    navegador LAN → POST válido → reinicio → `ONLINE` sin intervención adicional.
18. (Task 6B) El servidor HTTP no responde solicitudes cuando el dispositivo
    está en modo normal (NORMAL/ONLINE).

## 16. Fuera de alcance

- PAUD y audio PCM.
- I2S, micrófono y parlante.
- conversaciones OpenAI desde la placa.
- barge-in y `conversation.audio.clear`.
- cámara y JPEG.
- pulsador de timbre y LEDs de producto.
- PoE.
- Secure Boot, Flash Encryption y NVS Encryption productivos.
- acceso físico, relés, cerraduras o motores.

## 17. Correcciones a la especificación inicial

El plan de implementación deberá reflejar estas correcciones verificadas:

- No crear un firmware duplicado: migrar la app y compartir componentes.
- FW-1 anuncia `capabilities: []`; el backend sólo admite actualmente
  `audio_pcm16_v1`.
- La reconexión automática validada pertenece al simulador Python; el cliente
  web requiere acción de UI. El firmware reutilizará la política Python.
- El límite web de reproducción se amplió a diez segundos, pero ese dato no se
  trasladará a FW-1 ni condicionará futuros buffers físicos.
- `ONLINE` reemplazará la denominación local `IDLE` para evitar dos estados
  equivalentes.

## 18. Orden obligatorio posterior

Después de aprobar este documento:

1. escribir el plan detallado de implementación de FW-1;
2. instalar y verificar ESP-IDF 5.5.4;
3. crear un worktree aislado;
4. ejecutar la migración estructural mediante TDD;
5. implementar aprovisionamiento, Ethernet, WebSocket, HMAC y heartbeat en
   pasos revisables;
6. compilar y ejecutar pruebas automáticas;
7. flashear la placa y ejecutar la aceptación física;
8. no comenzar audio hasta aprobar FW-1.
