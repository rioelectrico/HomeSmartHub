# Portero ESP32-P4-ETH — FW-1

Firmware para la placa Waveshare ESP32-P4-POE-ETH (SKU 32088 / base SKU 32086 + módulo PoE oficial).
Conecta el dispositivo al backend Portero mediante Ethernet + WebSocket con autenticación HMAC-SHA256.

---

## Hardware requerido

| Componente | Modelo |
|---|---|
| Módulo | Waveshare ESP32-P4-POE-ETH v1.3 |
| PHY | IP101GR, RMII, dirección 1 |
| PoE | Módulo PoE oficial Waveshare (incluido en SKU 32088) |
| PSE | Switch o inyector PoE 802.3af/at compatible |
| USB | Solo para flash/diagnóstico (desconectar en pruebas PoE) |

---

## Toolchain

```powershell
# Activar entorno ESP-IDF 5.5.5 en una sesión PowerShell nueva
$env:IDF_PATH = "C:\Espressif\frameworks\esp-idf-v5.5.5"
. C:\Espressif\Initialize-Idf.ps1
idf.py --version   # debe imprimir ESP-IDF v5.5.5
```

---

## Build y flash

```powershell
# Desde el directorio de la aplicación física:
Set-Location firmware\esp32-p4

# Primera vez (o después de cambiar sdkconfig):
idf.py set-target esp32p4
idf.py build

# Flash (COM5 en este entorno; ajustar al puerto real):
idf.py -p COM5 flash

# Monitor serial:
idf.py -p COM5 monitor
# Salir del monitor: Ctrl+]
```

Build limpio desde cero:

```powershell
idf.py fullclean
idf.py build
```

---

## Aprovisionamiento

El dispositivo arranca en modo **PROVISIONING** si no tiene credenciales en NVS.

### Portal web (recomendado)

1. Conectar el cable Ethernet al dispositivo.
2. Abrir el monitor serial y anotar la IP asignada por DHCP:
   ```
   I (...) portero: [ETH] IP: 192.168.1.XX
   ```
3. En un navegador de la misma LAN abrir `http://192.168.1.XX/`.
4. Rellenar los tres campos:
   - **Device ID** — formato `PI-NNNNNN` (seis dígitos)
   - **Backend URL** — formato `ws://HOST:PUERTO/ws/device`
   - **Secret** — mínimo 32 caracteres, máximo 128
5. Hacer clic en **Guardar**.
6. El dispositivo reinicia automáticamente y entra en modo normal.

> **Seguridad:** el secreto nunca aparece en logs ni en la URL.
> HTTP sin TLS se acepta únicamente para el prototipo FW-1 en LAN;
> no es el modelo de seguridad de producción.

### Reset de fábrica

Mantener pulsado el botón BOOT (GPIO 0) durante 3 segundos mientras el dispositivo
está en marcha. El NVS se borra y el dispositivo reinicia en modo PROVISIONING.

### Borrado manual de NVS (laboratorio)

```powershell
python -m esptool --chip esp32p4 -p COM5 erase_region 0x9000 0x6000
```

---

## Operación normal

Tras el aprovisionamiento el dispositivo:

1. Espera a obtener IP por DHCP.
2. Abre una conexión WebSocket al backend.
3. Ejecuta el handshake HMAC-SHA256 (hello → challenge → auth.response → auth.ok).
4. Envía `device.status` y comienza a enviar heartbeats cada N segundos.
5. Atiende comandos administrativos (`device.status.request`, `camera.capture`).

El indicador de presencia en el panel de administración debe mostrar **ONLINE**.

---

## Reconexión automática

El firmware implementa backoff exponencial:

| Intento | Espera base |
|---|---|
| 1 | 1 s |
| 2 | 2 s |
| 3 | 4 s |
| 4 | 8 s |
| 5 | 15 s |
| 6+ | 30 s |

Las conexiones TCP semi-abiertas (backend caído sin cierre graceful) se detectan
mediante fallos de heartbeat y se recuperan con `stop()+start()` en una tarea
FreeRTOS separada.

---

## NVS — resistencia a corte de corriente

`device_config_save()` usa tres commits atómicos:

1. `ok=0` en slot inactivo → commit (invalida el slot antes de tocar datos).
2. `id/url/secret/gen/ok=1` → commit (registro completo durable).
3. `active=inactivo` → commit (puntero activo).

Un corte de corriente en cualquier punto deja siempre un slot válido (el anterior).
El contador de generación (`gen`) desempata cuando ambos slots son válidos.

### Opción de laboratorio — simular corte entre commits 2 y 3

```kconfig
# firmware/components/device_config/Kconfig
CONFIG_PORTERO_POWER_CUT_TEST=y
```

Cuando está habilitada, `device_config_save()` imprime `POWER_CUT_TEST_READY`
y se bloquea indefinidamente antes del tercer commit. Deshabilitar y rebuildar tras
la prueba.

---

## Tests Unity

```powershell
# Test app de componentes (device_auth, device_config, portero_codec, etc.):
Set-Location firmware\test_apps\fw1_components
idf.py build
# Flash + captura de salida (ejemplo con pyserial):
python read_serial.py   # ver herramienta en docs/verification/
```

Resultado esperado: `N Tests 0 Failures 0 Ignored`.

---

## Partición NVS

```
Offset: 0x9000   Tamaño: 0x6000 (24 KiB)
Namespace: portero_cfg
Claves: active, a_id, a_sec, a_url, a_ok, a_gen, b_id, b_sec, b_url, b_ok, b_gen
```

> El NVS no está cifrado en FW-1. Esta es una frontera de laboratorio;
> el secreto puede extraerse físicamente. No es el diseño de producción.

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| No aparece IP en serial | Cable sin link o DHCP inactivo | Verificar switch/PoE y cable |
| `Connecting...` indefinido | URL incorrecta o backend apagado | Revisar `backend_url` y estado del backend |
| `auth.fail` / timeout | Secreto incorrecto | Re-provisionar con el secreto correcto |
| Stack overflow en websocket_task | `task_stack` insuficiente | Verificar que `task_stack=32768` en ws_cfg |
| COM5 busy al flashear | Monitor serial abierto | Cerrar monitor antes del flash |
