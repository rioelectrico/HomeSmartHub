# INFORME DE ESTADO — Portero Inteligente ESP32-P4

**Fecha:** 2026-09-10  
**Repo GitHub:** https://github.com/rioelectrico/HomeSmartHub

---

## 1. HARDWARE

| Parámetro | Valor |
|-----------|-------|
| Chip | ESP32-P4 rev 1.3 |
| Board | Waveshare ESP32-P4-ETH |
| PHY Ethernet | IP101GR (RMII) |
| Flash física | 32 MB (binarios compilados con 32MB desde FW-2) |
| Puerto serial | COM5 |
| PSRAM | 8MB OPI (LDO habilitado en hw, pero CONFIG_SPIRAM no habilitado en firmware aún) |
| Backend URL | `ws://192.168.1.36:8000/ws/device` |

---

## 2. ENTORNO DE DESARROLLO

| Herramienta | Versión / Path |
|-------------|----------------|
| ESP-IDF | v5.5.5 en `C:\Espressif\frameworks\esp-idf-v5.5.5` |
| Python env | `C:\Espressif\python_env\idf5.5_py3.14_env\Scripts\python.exe` |
| CMake | `C:\Espressif\tools\cmake\3.30.2\bin` |
| Ninja | `C:\Espressif\tools\ninja\1.12.1` |
| Toolchain RISC-V | `riscv32-esp-elf esp-14.2.0_20260121` |

**Activación IDF (siempre necesaria antes de build):**
```powershell
$env:IDF_PATH = "C:\Espressif\frameworks\esp-idf-v5.5.5"
. C:\Espressif\Initialize-Idf.ps1
```

**Gotchas críticos:**
- `idf.py flash` falla en este entorno → usar `python -m esptool` directamente
- COM5 queda bloqueado si el monitor anterior sigue corriendo → `Get-Process python | Stop-Process -Force`
- Build link necesita ~3-4GB RAM libre. Windows Defender + Edge (~900MB) + Outlook (~350MB) pueden matar el proceso → correr `ninja -j1` en terminal separada con esos procesos cerrados

---

## 3. ESTRUCTURA DEL REPOSITORIO

```
C:\Users\cristian.lopez\Desktop\Portero\
├── .worktrees\
│   ├── esp32-p4-eth-fw1\        ← Worktree FW-1 (master, merger completo)
│   ├── esp32-p4-audio-fw2\      ← Worktree FW-2 (rama activa de audio)
│   └── sim1-realtime-voice\     ← Worktree backend SIM-1 realtime voice
├── docs\
│   └── superpowers\plans|specs\
└── firmware\                    ← repo raíz (esqueleto inicial)
```

---

## 4. FW-1 — CONECTIVIDAD BASE (COMPLETO)

**Rama:** `feat/esp32-p4-eth-fw1` | **Último commit:** `8978c90` (Task 12 docs)  
**Estado worktree local:** Mergeado a master → `64ba308`

### 4.1 Tasks completadas

| Task | Descripción | Commit |
|------|-------------|--------|
| 1 | Migración estructural + worktree | — |
| 2 | FSM de estados de conexión + Unity | — |
| 3 | Boot ID (128-bit RNG) + HMAC-SHA256 | — |
| 4 | `device_config` NVS dual-slot | — |
| 5 | `portero_codec` JSON estricto | — |
| 6 | Ethernet DHCP + `network_manager` | `7010e2f` |
| 6B | Portal HTTP de aprovisionamiento web | `c890132` |
| 6C | Factory reset por botón BOOT (GPIO 0) | `d02ad8f` |
| 7 | `ws_transport` + handshake HMAC → estado ONLINE | `387b7c4` |
| 8+10 | Watchdog handshake, `device.status`, backoff exponencial, half-open TCP | `2c09be5` + `24c28b8` |
| 9 | `command.ack` automático, `ws_transport_send_command_result()` | incluido |
| 11 | NVS hardening: generation counter + 4 fault-injection tests | `4f31cbe` |
| 12 | README + `docs/verification/esp32-p4-eth-fw1.md` | `8978c90` |

**Unity suite:** ✅ **110 Tests — 0 Failures — 0 Ignored** (verificado en hardware)

### 4.2 Tasks 12 físicas PENDIENTES (requieren hardware)

El usuario debe completar en `docs/verification/esp32-p4-eth-fw1.md`:
1. PoE-only: 10 arranques en frío + 30 min presencia continua
2. 25 reconexiones controladas + medición heap (diferencia ≤ 4 KiB)
3. Power-cut NVS: habilitar `CONFIG_PORTERO_POWER_CUT_TEST`, aprovisionar, cortar en `POWER_CUT_TEST_READY`, verificar recovery

### 4.3 Componentes FW-1 (en `firmware/components/`)

| Componente | Archivos | Descripción |
|------------|----------|-------------|
| `device_protocol` | `device_protocol.c`, `device_protocol.h` | FSM de estados |
| `device_auth` | `device_auth.c`, `device_auth.h` | Boot ID + HMAC-SHA256 |
| `device_config` | `device_config.c`, `device_config.h`, `Kconfig` | NVS dual-slot + validación |
| `portero_codec` | `portero_codec.c`, `portero_codec.h` | Codec JSON |
| `ws_transport` | `ws_transport.c`, `ws_transport.h` | WebSocket con backoff + watchdog |
| `network_manager` | `network_manager.c`, `network_manager.h` | DHCP Ethernet |
| `provisioning_web` | `provisioning_web.c`, `provisioning_web.h` | Portal HTTP |
| `hardware_hal` | `hardware_hal_esp32p4.c`, `hardware_hal.h` | HAL de hardware |

**`firmware/esp32-p4/main/`:** `app_main.c`, `board_port.c`  
**`firmware/test_apps/fw1_components/`:** Suite Unity 110 tests

### 4.4 Detalles técnicos ws_transport

- Stack de tarea: `32768` bytes (cJSON necesita ~25KB)
- Backoff: 1→2→4→8→15→30 s (cap en 30 s)
- Half-open TCP detectado por fallo de `heartbeat send` (n < 0) → `arm_reconnect()`
- `ws_reset_task` en tarea FreeRTOS separada (no en timer callback)
- Guard `esp_timer_is_active()` para evitar doble-arming

---

## 5. FW-2 — AUDIO BIDIRECCIONAL (EN CURSO)

**Rama:** `feat/esp32-p4-audio-fw2`  
**Worktree:** `C:\Users\cristian.lopez\Desktop\Portero\.worktrees\esp32-p4-audio-fw2\`  
**Último commit:** `4243138` — `feat(fw2): align conversation protocol to SIM-1 v2 + peripheral status`  
**Estado git:** limpio, en sync con `origin/feat/esp32-p4-audio-fw2`

### 5.1 Binario actual (build 09/09/2026)

| Archivo | Tamaño | Fecha |
|---------|--------|-------|
| `portero_esp32_p4.bin` | 953.6 KB | 09/09/2026 18:41:49 |
| `bootloader/bootloader.bin` | — | — |
| `partition-table.bin` | — | — |

### 5.2 Historial de commits FW-2

```
4243138  feat(fw2): align conversation protocol to SIM-1 v2 + peripheral status
0287b6d  feat(fw2-11+12+13+14): bidirectional audio pipeline + audio.clear
a49842d  feat(fw2-10+15): conversation FSM + barge-in + portero-ring console
b2073f7  feat(fw2-9): conversation codec + audio_pcm16_v1 capability
c7ba780  feat(fw2-8): ws_transport binary TX/RX for PAUD audio frames
9e7c26d  feat(fw2-7): paud_codec component - PAUD binary protocol encode/decode
bfb5cd6  refactor(fw2): remove MIC/SPK test code from app_main
e7a6469  test(fw2-5): SPK-1 physical - ES8311 DAC + NS4150B PA PASSED
13b29ee  test(fw2-4): MIC-1 physical - ES8311 ADC PASSED
96afb87  test(fw2-3): board_audio physical init - ES8311 confirmed
```

### 5.3 Estado de issues FW-2

| Issue | Estado |
|-------|--------|
| Protocolo SIM-1 completo | ✅ RESUELTO |
| Mic/speaker READY en frontend | ✅ RESUELTO |
| WS TX lock contención | ✅ RESUELTO (`SEPARATE_TX_LOCK`) |
| Audio robotizado (resampling 48→24kHz) | ✅ RESUELTO (24kHz nativo) |
| Stack overflow `audio_tx_enc` (4096) | ✅ RESUELTO (→ 8192) |
| Audio entrecortado/superpuesto | ✅ RESUELTO (`SPK_QUEUE_DEPTH` 4→256) |
| **Stack overflow `audio_tx_enc` (8192)** | ⚠️ PENDIENTE (→ 16384) |
| **AEC (echo cancellation)** | ⚠️ REVERTIDO (necesita PSRAM habilitada) |

### 5.4 Fix pendiente más urgente

**Stack overflow `audio_tx_enc`** en `firmware/esp32-p4/components/app_conversation/app_conversation.c`:
```c
// CAMBIAR:
xTaskCreate(audio_tx_task, "audio_tx_enc", 8192, NULL, 8, &s_tx_task_h);
// POR:
xTaskCreate(audio_tx_task, "audio_tx_enc", 16384, NULL, 8, &s_tx_task_h);
```
El crash ocurre cuando `transport_poll_write(0)` falla por backpressure TCP → el call stack de `abort_connection()` supera los 8192 bytes disponibles.

### 5.5 AEC — Estado y plan futuro

El AEC crasheaba en `esp_aec3_nlp_init` (Store access fault, NULL deref) porque el módulo NLP interno necesita un bloque DRAM contiguo de >96KB, y sin PSRAM habilitada no hay suficiente DRAM disponible.

**Para habilitarlo en el futuro:**
1. Agregar en `sdkconfig`: `CONFIG_SPIRAM=y`, `CONFIG_SPIRAM_USE_MALLOC=y`, `CONFIG_SPIRAM_IGNORE_NOTFOUND=y`
2. Pipeline: mic @ 24kHz → resample → 16kHz → AEC(mic, ref) → backend; ref del speaker (PCM16 @ 16kHz del backend)
3. Init en `app_main` antes de network/WebSocket (heap menos fragmentado)
4. Params: `filter_length=2`, `sample_rate=16000`, `AEC_MODE_FD_LOW_COST`, `AEC_NLP_LEVEL_NORMAL`, `mic_num=1`, `ref_num=1`

### 5.6 Componentes específicos de FW-2 (en `firmware/esp32-p4/components/`)

| Componente | Archivos | Descripción |
|------------|----------|-------------|
| `app_conversation` | `app_conversation.c`, `app_conversation.h` | Motor de conversación bidireccional (principal) |
| `board_audio` | `board_audio.c`, `board_audio.h` | Driver ES8311: mic/speaker, PA GPIO53 |
| `board_port` | `board_port.c`, `board_port.h` | Periféricos de board |
| `paud_codec` | `paud_codec.c`, `paud_codec.h` | Protocolo binario PAUD encode/decode |

**Componentes compartidos FW-1+FW-2** (en `firmware/components/`):

| Componente | Descripción |
|------------|-------------|
| `device_auth` | Boot ID + HMAC (sin cambios) |
| `device_config` | NVS dual-slot (sin cambios) |
| `device_protocol` | FSM (sin cambios) |
| `hardware_hal` | HAL (sin cambios) |
| `network_manager` | DHCP Ethernet (sin cambios) |
| `portero_codec` | JSON codec (modificado para SIM-1) |
| `provisioning_web` | Portal HTTP (sin cambios) |
| `ws_transport` | WebSocket (modificado: TX/RX binario para PAUD) |

### 5.7 Configuración actual del firmware FW-2

**`app_conversation.c`:**
- `TX_QUEUE_DEPTH`: 8 frames = 160ms (mic → backend)
- `SPK_QUEUE_DEPTH`: **256 frames = 5s** (backend → speaker)
- Stack `audio_tx_enc`: 8192 (PENDIENTE → 16384)
- Priority `audio_tx_enc`: 8 (sobre WS task ~5)
- `BARGE_IN_RMS_THRESHOLD`: 2000
- Fade suave en underrun y barge-in
- Telemetría `[audio-dbg]` cada 5 segundos

**`board_audio.c`:**
- Sample rate: **24000 Hz** (nativo, sin resampling)
- Mic gain: 3.0 dB
- Speaker vol: 100 (0 dB)
- PA (GPIO53) habilitado solo durante conversación activa

**`sdkconfig.defaults`:**
```
CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y
CONFIG_ESP32P4_REV_MIN_100=y
CONFIG_ESP_WS_CLIENT_SEPARATE_TX_LOCK=y
CONFIG_ESP_WS_CLIENT_TX_LOCK_TIMEOUT_MS=2000
```

---

## 6. COMANDOS DE BUILD Y FLASH

### Build FW-2
```powershell
$env:PATH = "C:\Espressif\tools\cmake\3.30.2\bin;" +
            "C:\Espressif\tools\ninja\1.12.1;" +
            "C:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20260121\riscv32-esp-elf\bin;" +
            "C:\Espressif\python_env\idf5.5_py3.14_env\Scripts;" + $env:PATH
$env:IDF_PATH = "C:\Espressif\frameworks\esp-idf-v5.5.5"
cd "C:\Users\cristian.lopez\Desktop\Portero\.worktrees\esp32-p4-audio-fw2\firmware\esp32-p4"
python.exe C:\Espressif\frameworks\esp-idf-v5.5.5\tools\idf.py build
```

### Flash FW-2 (con flash_args)
```powershell
cd "C:\Users\cristian.lopez\Desktop\Portero\.worktrees\esp32-p4-audio-fw2\firmware\esp32-p4\build"
python -m esptool --chip esp32p4 -p COM5 -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 32MB --flash_freq 80m "@flash_args"
```

### Flash manual (sin reconstruir)
```powershell
python -m esptool --chip esp32p4 -p COM5 -b 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 32MB --flash_freq 80m 0x2000 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin 0x10000 portero_esp32_p4.bin
```

---

## 7. DIAGNÓSTICO DE AUDIO (causa raíz resuelta)

El audio entrecortado era causado por ráfagas del backend TTS (todos los frames de una respuesta enviados a la vez, mucho más rápido que real-time). Con `SPK_QUEUE_DEPTH=4` (80ms buffer) el 79% de los frames se dropeaban.

```
# Log ANTES del fix:
spk: rx=2176  ovfl=1717  (79% drops)  → audio choppy, superpuesto

# Log DESPUÉS del fix (SPK_QUEUE_DEPTH=256):
spk: rx=262 ovfl=0 undr=183 q=195   → OK, cero drops
spk: rx=332 ovfl=0 undr=183 q=15    → OK
```

---

## 8. PRÓXIMAS ACCIONES

| Prioridad | Acción |
|-----------|--------|
| Alta | Aplicar fix `audio_tx_enc` stack 8192→16384 y hacer nuevo build/flash |
| Media | Completar verificaciones físicas de FW-1 (`docs/verification/esp32-p4-eth-fw1.md`) |
| Baja | Habilitar PSRAM en sdkconfig para poder integrar AEC en el futuro |
