# Evidencia de verificación — ESP32-P4-ETH FW-1

**Fecha:** 2026-09-07  
**Rama:** `feat/esp32-p4-eth-fw1`  
**Dispositivo:** Waveshare ESP32-P4-POE-ETH v1.3 (SKU 32088)  
**ID dispositivo:** PI-XXXXXX *(no incluir ID real asociado a un secreto activo)*

---

## 1. Toolchain y versiones

| Componente | Versión |
|---|---|
| ESP-IDF | v5.5.5-dirty |
| Target | esp32p4 |
| Revisión chip | v1.3 |
| Python | 3.14.4 |
| Commit firmware | 24c28b8-dirty (2026-09-07) |

Build reproducible:

```
Project build complete.
portero_esp32_p4.bin binary size 0xb9780 bytes.
Smallest app partition is 0x1f0000 bytes. 0x136880 bytes (63%) free.
```

---

## 2. Suite Unity — fw1_components (2026-09-07)

Build limpio: `idf.py fullclean && idf.py build`  
Flash: esptool directo a COM5  
Captura: Python pyserial con reset via DTR/RTS

```
110 Tests 0 Failures 0 Ignored
OK
```

Cobertura incluida:

| Grupo | Tests |
|---|---|
| device_auth | 7 |
| device_config (validación + NVS) | 20 |
| device_config (fault injection Task 11) | 4 |
| portero_codec | 39 |
| network_manager | 9 |
| provisioning_web | 17 |
| ws_transport (lifecycle) | 9 |
| **Total** | **110** |

Tests de fault injection verificados en hardware:

- `test_interrupted_write_preserves_active` — PASS
- `test_corrupted_active_falls_back_to_alternate` — PASS
- `test_both_slots_corrupted_returns_not_found` — PASS
- `test_higher_generation_wins_on_dual_valid` — PASS

---

## 3. Secret scan (2026-09-07)

```powershell
git grep -n -I -E "OPENAI_API_KEY|device_secret\s*=|sk-[A-Za-z0-9_-]{20,}"
```

Resultado: todos los matches son nombres de variable, ejemplos sintéticos
(`sk-example-not-a-real-key`, `aaaabbbbccccddddaaaabbbbccccdddd`) o tests con
valores de prueba explícitamente nombrados. Sin secretos reales en el repositorio.

---

## 4. Handshake WebSocket — primera conexión (registrado en sesión anterior)

```
I (...) ws_transport: [WS] CONNECTED
I (...) ws_transport: [WS] TX hello seq=0
I (...) ws_transport: [WS] RX auth.challenge nonce=...
I (...) ws_transport: [WS] TX auth.response seq=1
I (...) ws_transport: [WS] RX auth.ok heartbeat=15s
I (...) ws_transport: [WS] ONLINE — device.status enviado
I (...) portero: Device ONLINE (heartbeat every 15 s)
```

Panel administración: dispositivo muestra **ONLINE**. ✓

---

## 5. Reconexión automática — backoff exponencial (registrado en sesión anterior)

Prueba: backend bajado y vuelto a subir manualmente.

Secuencia observada:
- Backend cae → heartbeat falla → `arm_reconnect()` → intento 1 (1 s)
- Backend no disponible → intento 2 (2 s) → ... → intento 5 (15 s)
- Backend disponible → CONNECTED → ONLINE sin reset del ESP32. ✓

---

## 6. Prueba PoE-only — 30 minutos (PENDIENTE — rellenar por operador)

**Instrucciones:**
1. Desconectar cable USB del ESP32-P4.
2. Conectar solo el cable Ethernet con alimentación PoE.
3. Ejecutar 10 arranques en frío (desconectar/reconectar el cable Ethernet).
4. Para cada arranque registrar: resultado DHCP, resultado handshake, presencia ONLINE.
5. Dejar el dispositivo encendido 30 minutos observando heartbeats en el panel.

| Ciclo | DHCP | Handshake | ONLINE | Heap libre (bytes) |
|---|---|---|---|---|
| 1 | ☐ | ☐ | ☐ | |
| 2 | ☐ | ☐ | ☐ | |
| 3 | ☐ | ☐ | ☐ | |
| 4 | ☐ | ☐ | ☐ | |
| 5 | ☐ | ☐ | ☐ | |
| 6 | ☐ | ☐ | ☐ | |
| 7 | ☐ | ☐ | ☐ | |
| 8 | ☐ | ☐ | ☐ | |
| 9 | ☐ | ☐ | ☐ | |
| 10 | ☐ | ☐ | ☐ | |

**Prueba 30 min:**  
Inicio: ______  Fin: ______  
Heartbeats sin interrupción: ☐  
Reconexiones inesperadas: ___  
Módulo PoE: ______  PSE: ______  

---

## 7. 25 reconexiones controladas — drift de heap (PENDIENTE — rellenar por operador)

**Instrucciones:**
1. Con el backend activo, reiniciar el backend 28 veces (3 warm-up + 25 medidos).
2. Tras cada ONLINE, leer el heap libre del serial:
   ```
   I (...) ws_transport: free_heap=XXXXXX bytes
   ```
3. Registrar solo los ciclos 4–28 (25 medidos).

| Ciclo | Heap libre (bytes) |
|---|---|
| 4 | |
| 5 | |
| ... | |
| 28 | |

Mediana ciclos 1–5: ______  
Mediana ciclos 21–25: ______  
Diferencia: ______ (aceptable ≤ 4096 bytes) ☐  
Tendencia descendente continua: ☐ Sí / ☐ No  

---

## 8. Aprovisionamiento e interrupción (PENDIENTE — rellenar por operador)

**Prueba de corte de corriente en commit 3:**

1. Habilitar `CONFIG_PORTERO_POWER_CUT_TEST=y`, buildear y flashear.
2. Aprovisionar con credenciales sintéticas de laboratorio.
3. Esperar el log `[CONFIG] POWER_CUT_TEST_READY`.
4. Cortar la alimentación.
5. Restaurar alimentación y verificar que el dispositivo arranca con la generación anterior.
6. Deshabilitar la opción y rebuildar.

Resultado: ☐ Arrancó con generación anterior completa / ☐ Falló  
Generación activa tras recuperación: ______  

**Prueba de backoff con backend apagado al arrancar:**

1. Apagar el backend.
2. Arrancar el ESP32.
3. Observar al menos 3 intervalos de backoff en el serial.
4. Arrancar el backend.
5. Verificar ONLINE sin reset manual del ESP32.

Intervalos observados (s): ___, ___, ___  
ONLINE sin reset: ☐ Sí  

---

## 9. Criterios de aceptación FW-1

| Criterio | Estado |
|---|---|
| Build reproducible ESP-IDF 5.5.5 | ✓ |
| 110 Unity tests sin fallos | ✓ |
| Handshake HMAC-SHA256 → ONLINE | ✓ |
| Heartbeat activo post-auth | ✓ |
| Reconexión exponential backoff | ✓ |
| Detección half-open TCP (heartbeat fail) | ✓ |
| Aprovisionamiento web sin secreto en logs | ✓ |
| NVS two-slot atómico (3 commits) | ✓ |
| Fault injection 4 tests PASS | ✓ |
| Sin secretos en repositorio | ✓ |
| PoE-only 10 cold boots | PENDIENTE |
| PoE-only 30 min presencia continua | PENDIENTE |
| 25 reconexiones drift heap ≤ 4 KiB | PENDIENTE |
| Power-cut recovery NVS | PENDIENTE |
