# Portero Inteligente — MVP1 + voz Realtime SIM-1

Portero Inteligente es un panel local para administrar una vivienda y un dispositivo de
portería. Incluye FastAPI, PostgreSQL, Next.js, el simulador Python de MVP1, captura JPEG,
eventos, estadísticas, diagnóstico y auditoría. SIM-1 agrega un simulador web independiente
con conversación de voz manos libres, OpenAI Realtime, VAD, barge-in y transcripciones
durables. SIM-2 y la validación sobre hardware permanecen fuera de esta entrega.

El contrato de red está en [docs/device-protocol-v1.md](docs/device-protocol-v1.md) y los
límites entre componentes en [docs/architecture.md](docs/architecture.md).

## Puertos locales

| Componente | Dirección predeterminada |
|---|---|
| Panel Next.js | `http://localhost:3000` |
| Simulador web del portero | `http://localhost:3000/simulador-portero` |
| API FastAPI / OpenAPI | `http://localhost:8000` / `http://localhost:8000/docs` |
| WebSocket del dispositivo | `ws://localhost:8000/ws/device` |
| PostgreSQL | `localhost:5432` |

## Requisitos Windows

- Windows PowerShell 5.1 o PowerShell 7.
- Docker Desktop con `docker compose`.
- Python 3.12 o posterior disponible mediante `py`.
- Node.js 20.9 o posterior y npm.
- Git.

Los comandos siguientes parten de `C:\Portero`. No instalan paquetes Python globales.

## Preparación inicial

```powershell
Set-Location C:\Portero
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env.local
```

Antes de continuar, reemplace en `.env` y `backend\.env` todos los valores
`replace-with-*`. Use la misma contraseña local de PostgreSQL en `POSTGRES_PASSWORD`,
`DATABASE_URL` y `TEST_DATABASE_URL`; si contiene caracteres reservados, codifíquela para
una URL. Los ejemplos son ficticios y no son aptos para un despliegue real.

Genere `APP_SECRET_KEY` y `DEVICE_CREDENTIAL_ENCRYPTION_KEY` sin mostrarlos en consola. El
siguiente bloque funciona en Windows PowerShell 5.1 y PowerShell 7:

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$appBytes = New-Object byte[] 48
$fernetBytes = New-Object byte[] 32
$rng.GetBytes($appBytes)
$rng.GetBytes($fernetBytes)
$appSecret = [Convert]::ToBase64String($appBytes)
$fernetSecret = [Convert]::ToBase64String($fernetBytes).Replace('+', '-').Replace('/', '_')
$rng.Dispose()

foreach ($path in @('.env', 'backend\.env')) {
    $resolved = (Resolve-Path $path).Path
    $content = [IO.File]::ReadAllText($resolved)
    $content = $content.Replace('replace-with-a-random-secret', $appSecret)
    $content = $content.Replace('replace-with-a-fernet-key', $fernetSecret)
    [IO.File]::WriteAllText($resolved, $content, [Text.UTF8Encoding]::new($false))
}
Remove-Variable appSecret, fernetSecret, appBytes, fernetBytes
```

`OPENAI_API_KEY` es necesaria sólo para la conversación Realtime real.
El navegador nunca recibe `OPENAI_API_KEY`. La clave queda en `.env` y `backend\.env`,
ambos ignorados por Git, y sólo FastAPI la lee. El valor de ejemplo es un placeholder que
el diagnóstico considera no configurado. Cargue una clave real sin mostrarla en pantalla
ni dejarla en el historial:

```powershell
Set-Location C:\Portero
$protectedOpenAIKey = Read-Host 'OpenAI API key' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($protectedOpenAIKey)
try {
    $openAIKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    foreach ($path in @('.env', 'backend\.env')) {
        $resolved = (Resolve-Path $path).Path
        $content = [IO.File]::ReadAllText($resolved)
        $content = [regex]::Replace(
            $content,
            '(?m)^OPENAI_API_KEY=.*$',
            { param($match) "OPENAI_API_KEY=$openAIKey" }
        )
        [IO.File]::WriteAllText($resolved, $content, [Text.UTF8Encoding]::new($false))
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Variable protectedOpenAIKey, pointer, openAIKey -ErrorAction SilentlyContinue
}
```

`OPENAI_REALTIME_MODEL` permite elegir el modelo del backend; el valor predeterminado y
actual es `gpt-realtime-2.1`. Los límites iniciales son 300 segundos por conversación, 45
segundos de inactividad, 50 frames de entrada y 100 de salida. Mantenga
`OPENAI_REALTIME_SMOKE=false` durante uso y verificación normales.

## Base de datos

```powershell
Set-Location C:\Portero
docker compose config --quiet
docker compose up -d db
docker compose ps
```

Espere hasta que `db` figure `healthy`. Cree la base aislada de tests sin borrar el volumen
ni tocar la base de desarrollo:

```powershell
$testDatabase = 'portero_test'
if (-not $testDatabase.EndsWith('_test')) { throw 'Unsafe test database name' }
$exists = docker compose exec -T db psql -U portero -d portero -tAc "SELECT 1 FROM pg_database WHERE datname='$testDatabase'"
if ($exists.Trim() -ne '1') {
    docker compose exec -T db createdb -U portero $testDatabase
}
$env:TEST_DATABASE_URL = 'postgresql+psycopg://portero:YOUR_URL_ENCODED_DB_PASSWORD@localhost:5432/portero_test'
```

Nunca apunte `TEST_DATABASE_URL` a datos reales. Pytest y `scripts\verify.ps1` rechazan
esquemas no PostgreSQL, nombres que no terminan exactamente en `_test` y cualquier
override de destino en parámetros de consulta o fragmentos (por ejemplo, `dbname` o
`service`). Antes de migrar o limpiar tablas, ambos conectan y verifican que
`SELECT current_database()` también termine en `_test`.

## Dependencias locales

```powershell
Set-Location C:\Portero
py -3.12 -m venv backend\.venv
& backend\.venv\Scripts\python.exe -m pip install --upgrade pip
& backend\.venv\Scripts\python.exe -m pip install -e '.\backend[dev]'

py -3.12 -m venv tools\device_simulator\.venv
& tools\device_simulator\.venv\Scripts\python.exe -m pip install --upgrade pip
& tools\device_simulator\.venv\Scripts\python.exe -m pip install -e '.\tools\device_simulator[dev]'

Set-Location frontend
npm ci
Set-Location ..
```

## Migraciones y bootstrap

La aplicación de desarrollo y la base de tests se migran por separado:

```powershell
Set-Location C:\Portero\backend
& .\.venv\Scripts\python.exe -m alembic upgrade head

$savedDatabaseUrl = $env:DATABASE_URL
$env:DATABASE_URL = $env:TEST_DATABASE_URL
try {
    & .\.venv\Scripts\python.exe -m alembic upgrade head
} finally {
    $env:DATABASE_URL = $savedDatabaseUrl
}
```

El bootstrap es explícito e idempotente; no corre al iniciar FastAPI. El módulo público es
`python -m app.bootstrap` (el bloque usa el `python.exe` del venv). Mantenga la
contraseña sólo durante el comando y no la escriba en el historial:

```powershell
Set-Location C:\Portero\backend
$env:BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:BOOTSTRAP_ADMIN_EMAIL = 'admin@example.test'
$protected = Read-Host 'Bootstrap password' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($protected)
try {
    $env:BOOTSTRAP_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    & .\.venv\Scripts\python.exe -m app.bootstrap
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Item Env:BOOTSTRAP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    Remove-Variable protected, pointer
}
```

## Ejecutar la aplicación

Abra terminales distintas. Para desarrollo, el reloader administra un solo proceso
servidor efectivo:

```powershell
Set-Location C:\Portero\backend
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --loop app.event_loop:selector_loop_factory --host 127.0.0.1 --port 8000
```

Para una ejecución estable sin reload use explícitamente un worker:

```powershell
Set-Location C:\Portero\backend
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --workers 1 --loop app.event_loop:selector_loop_factory --host 127.0.0.1 --port 8000
```

En otra terminal:

```powershell
Set-Location C:\Portero\frontend
npm run dev
```

Ambos modos de Uvicorn usan **un solo proceso efectivo** para la aplicación. El registro de
WebSockets, la propiedad de conexiones y los tokens de upload son efímeros y viven en
memoria. Varios workers requieren antes un coordinador compartido para leases, despacho y
tokens; no use `--workers 2` o más en MVP1.

El factory `--loop app.event_loop:selector_loop_factory` es obligatorio en Windows:
psycopg async necesita un selector loop, mientras que Uvicorn elige Proactor por defecto
cuando corre un único worker. Omitirlo deja `/health` vivo pero impide `/ready` y todo acceso
a PostgreSQL.

Compruebe `http://localhost:8000/health`, `http://localhost:8000/ready` y luego abra
`http://localhost:3000`.

## Aprovisionar y ejecutar el simulador

1. Inicie sesión y abra **Dispositivos**.
2. Cree `PI-000001` con un nombre descriptivo.
3. Copie el secreto mostrado una sola vez. No se puede recuperar después; una rotación
   invalida el anterior.
4. En una nueva terminal, páselo oculto mediante el entorno, nunca como argumento CLI:

```powershell
Set-Location C:\Portero\tools\device_simulator
$env:DEVICE_ID = 'PI-000001'
$env:BACKEND_URL = 'http://127.0.0.1:8000/'
$protected = Read-Host 'One-time device secret' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($protected)
try {
    $env:DEVICE_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    & .\.venv\Scripts\portero-device-simulator.exe
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Item Env:DEVICE_SECRET -ErrorAction SilentlyContinue
    Remove-Variable protected, pointer
}
```

El simulador usa su JPEG incluido salvo que se indique `--image C:\ruta\captura.jpg`.
Puede habilitar diagnóstico con `--verbose`; aun así no registra credenciales ni frames de
autenticación.

## Ejecutar el simulador web de voz SIM-1

Con backend y frontend activos, abra `http://localhost:3000/simulador-portero` en un perfil
limpio. Esta pantalla es independiente del panel administrativo y representa el futuro
ESP32: autentica con el mismo challenge/HMAC y usa el mismo `/ws/device`. El navegador
nunca se conecta a OpenAI ni recibe la API key.

1. Escriba `PI-000001` y el secreto vigente; pulse **Conectar**. El campo secreto se borra
   en cuanto se entrega al cliente y no se guarda en cookies, storage, URL ni telemetría.
2. Pulse **Tocar timbre**. Es el único gesto que solicita micrófono e inicia audio, para
   cumplir las políticas de permisos y autoplay. Si el permiso se rechaza, puede reintentar
   sin recargar.
3. Hable normalmente. El indicador muestra nivel, los estados distinguen visitante/agente
   y la pantalla agrega sólo transcripciones finales.
4. Puede interrumpir al agente: el barge-in cancela la respuesta y
   `conversation.audio.clear` descarta inmediatamente el audio pendiente.
5. Pulse **Finalizar visita** para cerrar micrófono, parlante, stream y conversación.

Si el audio del agente llega más rápido de lo que puede reproducirse y supera la cola
local de dos segundos, la visita se detiene con `AUDIO_OUTPUT_OVERFLOW`; no se omiten
palabras para seguir reproduciendo. El micrófono y el parlante se apagan, el backend cierra
la conversación como fallida y conserva el código en `conversation_failed`. Espere a que
**Tocar timbre** vuelva a habilitarse para reintentar sobre la misma conexión autenticada.

El transporte usa PCM16 little-endian mono a 24 kHz, frames nominales de 20 ms y header
binario `PAUD`. `conversation_id` identifica la conversación durable; `stream_id` es el
stream efímero y debe ser distinto. Consulte el contrato byte a byte en
[`docs/device-protocol-v1.md`](docs/device-protocol-v1.md).

## Smoke real OpenAI Realtime, sólo opt-in

La suite y `scripts\verify.ps1` nunca llaman OpenAI. El comando también se niega salvo que
la configuración booleana validada `OPENAI_REALTIME_SMOKE` resulte `true`; puede definirse
temporalmente en el entorno del mismo proceso (forma recomendada) o en `.env`. El valor
predeterminado `false` siempre se niega: la habilitación explícita es
`OPENAI_REALTIME_SMOKE=true`. La prueba abre una sesión acotada, espera
disponibilidad, envía 20 ms de PCM cero y siempre cierra.
El smoke puede generar consumo facturable. Su salida sólo contiene estado y
`correlation_id`, nunca clave ni audio.

```powershell
Set-Location C:\Portero\backend
$env:OPENAI_REALTIME_SMOKE = 'true'
try {
    & .\.venv\Scripts\python.exe -m app.ai.smoke
} finally {
    Remove-Item Env:OPENAI_REALTIME_SMOKE -ErrorAction SilentlyContinue
}
```

Sin el flag, el exit code esperado es `2`. Con opt-in, `0` significa sesión disponible y
`1` un fallo saneado. La apertura y la confirmación `SessionReady` comparten un timeout
total de 10 segundos; el envío de audio y el cierre tienen límites independientes para
que la limpieza termine aun si el transporte se bloquea. Ejecute este smoke manualmente
una sola vez para la aceptación, no en CI ni como parte de verificaciones repetidas.

## Recorrido de aceptación MVP1

Con PostgreSQL, backend y frontend activos:

1. Confirme PostgreSQL `healthy`, `/health` 200 y `/ready` 200.
2. Aplique Alembic y ejecute el bootstrap; inicie sesión y abra el Dashboard.
3. En **Agente IA**, cambie nombre, prompt u otra opción y guarde. Recargue el navegador;
   para probar persistencia durable, reinicie sólo FastAPI y confirme que el cambio sigue.
4. Cree y aprovisione `PI-000001`; compruebe que el secreto aparece una sola vez.
5. Inicie `portero-device-simulator` y espere estado ONLINE y snapshot en Diagnóstico.
6. Detenga el simulador. Espere como máximo `DEVICE_OFFLINE_TIMEOUT` más un intervalo de
   mantenimiento y compruebe una sola transición OFFLINE. Una desconexión limpia puede
   detectarse antes. Vuelva a iniciarlo y compruebe una sola transición ONLINE.
7. En **Dispositivos**, envíe `device.status.request` y observe ACK/RESULT y snapshot nuevo.
8. En **Cámara**, solicite `camera.capture`; observe `pending`, `sent`, `acknowledged` y
   `completed` en la línea de tiempo (ACK y RESULT).
9. Abra el JPEG mediante su URL protegida. Una sesión sin cookie o sin `camera.view` debe
   recibir 401/403, no el archivo.
10. En **Eventos**, filtre `camera_capture`; revise también estadísticas, diagnóstico y
    auditoría. Deben aparecer sólo datos permitidos, sin secretos ni binarios.
11. Ejecute todas las suites con `scripts\verify.ps1`.

Las pantallas **Dashboard**, **Agente IA**, **Cámara**, **Eventos**, **Estadísticas**,
**Dispositivos** y **Diagnóstico** consumen datos reales del backend; la autorización del
frontend nunca reemplaza RBAC del servidor.

## Recorrido manual de aceptación SIM-1

Con PostgreSQL `healthy`, migraciones, backend de un solo worker, frontend y una API key
local real configurados:

1. Aprovisione o rote `PI-000001` y copie el secreto una sola vez.
2. Abra `http://localhost:3000/simulador-portero` en un perfil limpio, autentique el
   dispositivo y compruebe que el campo de secreto queda vacío.
3. Pulse **Tocar timbre** y anote los dos UUID de `conversation.started` desde las
   herramientas de desarrollo: `conversation_id` durable y `stream_id` efímero. Confirme
   que son distintos.
4. Diga exactamente: **Hola, soy Juan, vengo a entregar un paquete**. Verifique audio
   bidireccional y que aparecen las transcripciones finales del visitante y del agente.
5. Mientras el agente habla, vuelva a hablar. Verifique que el barge-in detiene el audio
   inmediatamente; puede continuar la conversación sin reconectar.
6. Pulse **Finalizar visita** y confirme que micrófono y reproducción se detienen.
7. Consulte PostgreSQL en modo lectura con el `conversation_id` observado. Debe existir
   exactamente una conversación cerrada, mensajes finales de ambos roles, ningún
   `provider_item_id` duplicado y no existen columnas de audio:

```powershell
Set-Location C:\Portero
$conversationId = Read-Host 'conversation_id observado'
$sim1ReadOnlySql = @'
\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SELECT c.id, c.status, c.outcome, c.created_at, c.closed_at, d.device_id
FROM conversations c JOIN devices d ON d.id = c.device_id
WHERE c.id = :'conversation_id';
SELECT role, content, metadata FROM conversation_messages
WHERE conversation_id = :'conversation_id' ORDER BY created_at, id;
SELECT metadata->>'provider_item_id' AS provider_item_id, count(*)
FROM conversation_messages WHERE conversation_id = :'conversation_id'
GROUP BY 1 HAVING count(*) > 1;
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('conversations', 'conversation_messages')
  AND column_name ILIKE '%audio%';
COMMIT;
'@
$sim1ReadOnlySql | docker compose exec -T db psql -X -U portero -d portero `
    -v ON_ERROR_STOP=1 -v "conversation_id=$conversationId"
Remove-Variable sim1ReadOnlySql, conversationId
```

El primer resultado debe tener una fila con `status=closed`; el tercero y cuarto deben
devolver cero filas. Revise además que ni consola, URL, eventos ni auditoría muestran el
secreto o `OPENAI_API_KEY`. Por último, ejecute `scripts\verify.ps1` y el smoke opt-in una
sola vez. Registre estas evidencias antes de aprobar SIM-1; SIM-2 no comienza sin aceptación
explícita.

## Verificación completa

Desde cualquier directorio, con PostgreSQL saludable y `TEST_DATABASE_URL` definido:

```powershell
& C:\Portero\scripts\verify.ps1
```

El script usa los venv del proyecto, hace el preflight estático y efectivo de la base,
y ejecuta Compose config, migración a head, tests del backend, Ruff, formato, mypy,
tests/calidad del simulador y tests con `TZ=UTC`, lint, typecheck y build del frontend.
No imprime variables sensibles y no ejecuta el smoke real de OpenAI ni una prueba física.

Para detener sólo la infraestructura de este proyecto sin borrar datos:

```powershell
Set-Location C:\Portero
docker compose down
```

No use `docker compose down -v`: el volumen nombrado contiene PostgreSQL.

## Limitaciones verificables

- SIM-1 entrega audio bidireccional, OpenAI Realtime y barge-in. La aprobación final exige
  todavía la evidencia manual del navegador y el smoke real voluntario del entorno local.
- Cámara conversacional, herramientas OpenAI y administración avanzada pertenecen a
  **SIM-2 — no implementado** y no deben iniciarse antes de aceptar SIM-1.
- No hay reconocimiento facial, cerradura real, video continuo, MQTT, OTA productivo,
  mTLS ni despliegue cloud.
- El modelo/revisión de la placa y su BSP siguen sin confirmarse. El entorno de esta
  entrega no tiene toolchain ESP-IDF ni hardware; no se verificaron compilación de firmware,
  pruebas Unity en placa, Ethernet, cámara, micrófono ni parlante físicos.
- La validación actual de firmware es estática y hardware-neutral. No implica soporte de
  una placa ESP32-P4 concreta.
