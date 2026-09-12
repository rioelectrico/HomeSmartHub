# Informe técnico - AEC full-duplex ESP32-P4

Fecha: 2026-09-11
Proyecto: Portero Inteligente
Estado: cierre de jornada

## 1. Objetivo

La etapa buscó habilitar conversación full-duplex real: Clara habla por el speaker, el visitante puede hablar o interrumpir y el eco de Clara no debe volver al backend como voz humana.

- ESP32-P4: captura, reproducción, AEC local, PAUD y WebSocket.
- AEC local: reduce el eco antes del uplink.
- Backend: lifecycle, persistencia, transporte y coordinación.
- OpenAI Realtime: VAD, respuestas, audio y transcripciones.
- Barge-in: cancela la respuesta ante voz humana válida.

## 2. Estado general

```text
Full-duplex completo: NO
Estado: PARCIAL
```

Validado: AEC local estable, uplink procesado, downlink de Clara, saludo inicial, preservación near-end aislada y double-talk aislado parcial.

Pendiente: Caso A assistant-only falla de forma intermitente por `conversation.audio.clear` falso. Interrupción humana conversacional y full-duplex final no están validados.

## 3. Hardware, formato y pipeline

Hardware confirmado:

- ESP32-P4.
- ES8311 en `0x30`.
- I2S TX/RX en I2S0.
- PCM16 LE, mono, 24 kHz, 480 samples, 20 ms.
- PAUD y framing productivo sin cambios.
- FIFO AEC y storage de speaker queue en PSRAM.

Pipeline MIC:

```text
I2S RX 24k -> 24->16 -> accumulator -> AEC -> 16->24
-> FIFO procesado -> pacer 20 ms -> PAUD -> WebSocket/backend
```

Pipeline REFERENCE:

```text
speaker mono final -> reference tap -> 24->16 -> history
-> delay -> AEC ref
```

Configuración vigente:

```text
AEC_MODE_FD_LOW_COST
filter_length=4
delay=981 samples @16 kHz
```

## 4. Delay y calibración

Delay validado:

```text
981 samples @16 kHz = 61.3125 ms
```

- El estímulo OLD fue descartado: acertaba a veces pero con cluster delta ambiguo.
- MLS-13 mantuvo mejor separación bajo FIR, multipath y ruido.
- Cinco mediciones físicas MLS convergieron a 981.
- La telemetría de cluster fue corregida para excluir sólo `best +/-1` sample.
- `2862 / 178.875 ms` es una medición histórica inválida, basada en OLD.
- El test epoch A/B con 4096 muestras validó hipótesis A: delay 981 sin ajuste extra.
- La hipótesis B (`981 + 320`, por callback lead de `480 @24k`) fue descartada.

Resultado pre-AEC:

```text
offset residual=-1 sample
corr ~= 442..447
```

## 5. Filter y far-end

`filter_length=2` mostró cancelación casi nula:

```text
raw RMS ~=17.6
out RMS ~=17.4
ERLE ~=0.1 dB
```

`filter_length=4` fue seleccionado:

```text
raw RMS=19.5
AEC output RMS=4.8
ERLE=12.13 dB
process_max=6325 us
frame=32 ms
underflow=0
overflow=0
watchdog=0
```

El far-end aislado queda aprobado: el speaker activo fue reducido de forma clara manteniendo margen de CPU.

## 6. Near-end y double-talk aislados

Near-end con speaker callado: la salida AEC mantuvo la voz humana; valores completos por corrida: `NO CONFIRMADO` en el contexto disponible.

Double-talk aislado:

- MLS continua durante toda la fase.
- Speaker activo y persona hablando.
- Evidencia de supervivencia near-end en 5 de 6 ventanas.
- Una ventana fue no concluyente.
- Sin clipping, underflow, overflow ni watchdog relevantes.

Esto valida el AEC aislado, no el barge-in productivo con OpenAI.

## 7. Estabilidad, stack y memoria

Fixes de stack:

- Buffers AEC grandes fuera del stack.
- `pcm_frame_t` de 968 bytes removido de `audio_rx`.
- `audio_aec_alignment_metrics_t` removido de `console_repl` y asignado en PSRAM.
- Logs de observabilidad diferidos fuera de `audio_rx`.

Smoke final:

```text
aec_blocks=303
panic=0
stack_protection=0
watchdog=0
audio_rx hwm_min=3688
aec_mic hwm_min=2120
process_max_us=4941
mic/ref underflow=0/0
mic/ref overflow=0/0
tx_underflow=0
```

FIFO AEC:

```text
3840 x int16_t = 7680 bytes
is_psram=1
alignment=16-byte OK
```

Speaker queue:

```text
length=256
item_size=968
storage=247808 bytes
```

La asignación dinámica fallaba porque `largest_internal` era insuficiente. Se cambió a `xQueueCreateStatic()` con storage en PSRAM y `StaticQueue_t` en RAM interna. Cinco ciclos de lifecycle no mostraron leaks progresivos.

## 8. app_conversation_init

Problema inicial:

```text
speaker_queue allocation -> ESP_ERR_NO_MEM
riesgo posterior de xQueueReset(NULL)
```

Resultado tras guards, cleanup y storage PSRAM:

```text
mutex OK
tx_queue OK
speaker_queue OK
app_conversation_init=ESP_OK
```

## 9. Backend y OpenAI Realtime

Bug 1: Clara no hablaba si el visitante callaba.

Causa: sólo existía VAD; no había solicitud inicial de respuesta.

Fix: `response.create` posterior a `conversation.started`, `listening` y workers.

Bug 2: conversación persistida `open` tras reinicio de backend.

Causa: el índice único activo por dispositivo provocaba `IntegrityError` en el siguiente inicio.

Fix: cerrar transaccionalmente conversaciones abiertas huérfanas del dispositivo como `abandoned` antes de crear la nueva.

Validación backend:

```text
3 passed
```

Archivos backend modificados:

- `backend/app/ai/conversation_coordinator.py`
- `backend/app/ai/realtime.py`
- `backend/app/ai/openai_realtime.py`
- `backend/app/ai/fake_realtime.py`
- `backend/tests/ai/test_conversation_coordinator.py`
- `backend/tests/ai/test_realtime_provider.py`
- `backend/tests/ai/test_smoke.py`

## 10. Audio real end-to-end

Downlink validado:

```text
speaker rx=212
speaker queued=212
speaker consumed=211
```

Prueba: OpenAI -> backend -> WebSocket -> ESP32 -> speaker queue -> reproducción.

Uplink observado:

```text
mic_sent_ok progresando
mic_send_error=0
```

Ejemplo: `mic cap=1000`, `mic send ok=993`.

## 11. Caso A assistant-only

Objetivo:

```text
Clara habla
visitante callado
0 SpeechStarted falsos
0 response.cancel falsos
0 audio.clear falsos
```

Resultado:

```text
responses_tested=3
false_audio_clear=2/3
Caso A=NO APROBADO
```

Una repetición se mantuvo estable por 20 segundos:

```text
audio_clear=0
barge=0
speaker rx/queued/consumed=320/320/320
```

Dos produjeron `conversation.audio.clear` falso.

## 12. Bloqueo y próximo gate

El bloqueo actual es un `conversation.audio.clear` falso mientras Clara habla y el visitante está callado.

No está demostrado si la causa es residual acústico, convergencia inicial, transitorio de speaker, referencia ausente/desalineada, falso `speech_started` de Realtime, routing backend u otra causa.

No cambiar todavía:

```text
VAD thresholds
server_vad
semantic_vad
delay=981
filter=4
volumen
```

Próximo paso único: instrumentar un Caso A correlacionando speaker activity, raw mic RMS, reference RMS, AEC output RMS, `speech_started`, `response.cancel` y `audio.clear` alrededor del falso evento. El resultado decidirá si el fix pertenece a AEC, backend o VAD/gating.

## 13. Estado por componente

| Componente | Estado |
|---|---|
| Captura mic | VALIDADO |
| Speaker | VALIDADO |
| WebSocket uplink | VALIDADO |
| WebSocket downlink | VALIDADO |
| AEC far-end | VALIDADO |
| Preservación near-end | PARCIAL |
| Double-talk aislado | PARCIAL |
| Pipeline AEC conversacional | VALIDADO |
| Clara hablando | VALIDADO |
| Falso barge-in assistant-only | FALLA |
| Interrupción humana real | PENDIENTE |
| Full-duplex completo | PENDIENTE |

## 14. Decisiones validadas

```text
delay=981 @16k
filter_length=4
AEC_MODE_FD_LOW_COST
AEC local antes de PAUD/WebSocket
AEC FIFO en PSRAM
speaker queue en PSRAM
worker AEC dedicado
PAUD 24k / 480 samples / 20 ms sin cambios
```

## 15. Deuda técnica

Bloqueante:

```text
Falsos conversation.audio.clear durante assistant-only.
```

No bloqueante:

```text
speaker queue=256 frames ~=5.12 s de capacidad.
```

## 16. Retomar

```powershell
$env:PROCESSOR_ARCHITECTURE = 'AMD64'
. C:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1
Set-Location C:\Portero\firmware\esp32-p4
idf.py build
idf.py -p COM3 flash monitor
```

```powershell
Set-Location C:\Portero\backend
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe run_backend.py
```

`run_backend.py` usa `WindowsSelectorEventLoop` para compatibilidad async en Windows.

## 17. Git al cierre

```text
branch=master
root=C:\Portero
```

No se creó commit de código en esta jornada dentro de este informe. Hay cambios de firmware/backend y logs de diagnóstico no trackeados que deben revisarse por separado antes de un commit de producto.
