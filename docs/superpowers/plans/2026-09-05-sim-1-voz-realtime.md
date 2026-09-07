# SIM-1 Voz Realtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar una conversación de voz manos libres completa entre `/simulador-portero`, el WebSocket autenticado de dispositivo, FastAPI y OpenAI Realtime, con transcripciones durables, barge-in y cleanup determinista.

**Architecture:** El protocolo V1 existente conserva challenge/HMAC, presencia, comandos y secuencia JSON de dispositivo. SIM-1 añade mensajes de conversación y frames binarios `PAUD`; un coordinador por proceso enlaza cada socket autenticado con una sesión `AIRealtimeProvider`, usando colas acotadas y sesiones SQLAlchemy cortas. El navegador actúa como ESP32-P4: autentica con Web Crypto, captura/reproduce PCM16 mediante AudioWorklet y nunca conoce `OPENAI_API_KEY`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL, Alembic, Pydantic 2, asyncio, websockets, pytest; Next.js 16, React 19, TypeScript 5.9, Web Crypto, Web Audio/AudioWorklet, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-05-openai-realtime-portero-simulator-design.md`

## Global Constraints

- Implementar sólo SIM-1. No agregar cámara, herramientas OpenAI, `record_visit`, endpoints administrativos ni correcciones visuales.
- Reutilizar `/ws/device`, challenge/HMAC, `DeviceConnectionRegistry`, presencia y configuración `AgentConfig` de MVP1.
- El navegador no se conecta a OpenAI; `OPENAI_API_KEY` existe sólo en el backend y nunca se registra, persiste o devuelve.
- Audio de transporte: PCM16 little-endian, mono, 24 kHz, frame nominal de 20 ms y payload nominal de 960 bytes.
- Header `PAUD`: 34 bytes big-endian, versión 1, tipo 1 entrada, tipo 2 salida, `stream_id` UUID efímero y `seq` `u64` creciente por dirección.
- `conversation_id` es durable y nunca se usa como `stream_id`.
- Límites predeterminados: 300 s por conversación, 45 s de inactividad, 50 frames de entrada y 100 de salida.
- Overflow de cualquier cola cancela la respuesta, limpia salida local/remota y cierra sólo la conversación con un código estable.
- Una conversación activa por dispositivo y por socket propietario; la reconexión no revive una conversación cerrada.
- Crear la conversación durable sólo después de abrir correctamente Realtime; fallos previos no dejan filas.
- Persistir únicamente transcripciones finales deduplicadas; nunca audio ni payloads externos crudos.
- La suite normal usa `FakeRealtimeProvider`; el smoke real requiere `OPENAI_REALTIME_SMOKE=true`.
- Modelo backend predeterminado `gpt-realtime-2.1`, configurable por `OPENAI_REALTIME_MODEL`.
- Mantener la ejecución con un único worker mientras la propiedad de sockets y conversaciones sea en memoria.

---

## File Map

### Backend: archivos nuevos

- `backend/alembic/versions/0006_add_sim1_conversation_fields.py`: enum de resultado, FK de dispositivo e índice único parcial para conversación activa.
- `backend/app/schemas/conversations.py`: mensajes de control salientes, estados, resultados y errores públicos.
- `backend/app/devices/audio_protocol.py`: encode/decode y validación del header binario `PAUD`.
- `backend/app/ai/realtime.py`: contrato interno del proveedor y eventos normalizados.
- `backend/app/ai/fake_realtime.py`: proveedor determinista para tests.
- `backend/app/ai/openai_realtime.py`: transporte WebSocket OpenAI y traducción de eventos externos.
- `backend/app/services/conversations.py`: creación, cierre y persistencia idempotente de transcripciones.
- `backend/app/ai/conversation_coordinator.py`: ownership, lifecycle, colas, VAD, barge-in y timers.
- `backend/app/ai/smoke.py`: smoke opt-in sin secretos.
- `backend/tests/devices/test_audio_protocol.py`: contrato binario.
- `backend/tests/ai/test_realtime_provider.py`: proveedor falso y adaptador OpenAI con transporte simulado.
- `backend/tests/ai/test_conversation_coordinator.py`: lifecycle y audio deterministas.
- `backend/tests/services/test_conversations.py`: persistencia mínima y deduplicación.

### Backend: archivos modificados

- `backend/app/schemas/devices.py`: capacidades en hello y mensajes `conversation.start/stop`.
- `backend/app/devices/protocol.py`: unión discriminada ampliada.
- `backend/app/devices/connections.py`: envío binario y comprobación de ownership.
- `backend/app/websocket/device.py`: recepción texto/binario y delegación al coordinador.
- `backend/app/models/activity.py`, `backend/app/models/__init__.py`: campos y enums SIM-1.
- `backend/app/config.py`, `backend/.env.example`, `.env.example`: configuración validada.
- `backend/app/ai/provider.py`: diagnóstico seguro y construcción del proveedor real.
- `backend/app/main.py`: composición y shutdown del coordinador.
- `backend/pyproject.toml`: dependencia WebSocket directa.
- `backend/tests/websocket/test_device_socket.py`, `backend/tests/test_config.py`, `backend/tests/test_models.py`: regresión e integración.

### Frontend: archivos nuevos

- `frontend/app/simulador-portero/page.tsx`: ruta independiente.
- `frontend/components/simulator/portero-simulator.tsx`: máquina de estados y UI.
- `frontend/lib/simulator/contracts.ts`: tipos estrictos del control JSON.
- `frontend/lib/simulator/device-client.ts`: HMAC, heartbeat, control y transporte binario.
- `frontend/lib/simulator/audio-frame.ts`: codec `PAUD` en `ArrayBuffer`.
- `frontend/lib/simulator/audio-codec.ts`: resampling y Float32/PCM16.
- `frontend/lib/simulator/audio-session.ts`: AudioContext, worklet, micrófono y reproducción.
- `frontend/public/portero-audio-worklet.js`: captura y buffer de reproducción con operación `clear`.
- `frontend/tests/simulator-protocol.test.ts`, `frontend/tests/simulator-audio.test.ts`, `frontend/tests/simulator-page.test.tsx`: contratos y UI.

### Frontend y documentación: archivos modificados

- `frontend/app/globals.css`: estilos aislados, responsive y accesibles del simulador.
- `frontend/.env.example`: `NEXT_PUBLIC_DEVICE_WS_URL` sin secretos.
- `frontend/tests/setup.ts`: mocks mínimos de Web Audio sólo para tests.
- `docs/device-protocol-v1.md`, `docs/architecture.md`, `README.md`: contrato activo y runbook SIM-1.

---

### Task 1: Configuración SIM-1 y diagnóstico seguro

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/ai/provider.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/.env.example`
- Modify: `.env.example`
- Test: `backend/tests/test_config.py`
- Test: `backend/tests/api/test_diagnostics.py`

**Interfaces:**
- Produces: `Settings.conversation_max_seconds: int`, `conversation_idle_seconds: int`, `audio_input_queue_frames: int`, `audio_output_queue_frames: int`, `openai_realtime_smoke: bool`.
- Produces: `resolve_realtime_model(agent_model: str, settings: Settings) -> str`.
- Produces: `ProviderStatus` con `UNCONFIGURED`, `CONFIGURED`, `AVAILABLE`, `UNAVAILABLE`.

- [ ] **Step 1: Write failing configuration and placeholder tests**

```python
def test_sim1_defaults(tmp_path: Path) -> None:
    settings = Settings(**valid_env(tmp_path))
    assert settings.openai_realtime_model == "gpt-realtime-2.1"
    assert settings.conversation_max_seconds == 300
    assert settings.conversation_idle_seconds == 45
    assert settings.audio_input_queue_frames == 50
    assert settings.audio_output_queue_frames == 100
    assert settings.openai_realtime_smoke is False

def test_placeholder_openai_key_is_unconfigured(tmp_path: Path) -> None:
    settings = Settings(**valid_env(tmp_path), OPENAI_API_KEY="sk-example-not-a-real-key")
    assert ConfiguredOpenAIRealtimeProvider(settings).diagnose() == ProviderStatus.UNCONFIGURED
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/api/test_diagnostics.py -v`

Expected: FAIL because the settings and four-state diagnosis do not exist.

- [ ] **Step 3: Add bounded settings, model resolution and placeholder detection**

```python
conversation_max_seconds: PositiveInt = Field(default=300, validation_alias="CONVERSATION_MAX_SECONDS")
conversation_idle_seconds: PositiveInt = Field(default=45, validation_alias="CONVERSATION_IDLE_SECONDS")
audio_input_queue_frames: PositiveInt = Field(default=50, validation_alias="AUDIO_INPUT_QUEUE_FRAMES")
audio_output_queue_frames: PositiveInt = Field(default=100, validation_alias="AUDIO_OUTPUT_QUEUE_FRAMES")
openai_realtime_smoke: bool = Field(default=False, validation_alias="OPENAI_REALTIME_SMOKE")
```

Validate `conversation_idle_seconds < conversation_max_seconds`, set the Realtime model default to `gpt-realtime-2.1`, and treat blank values plus the exact documented example key as unconfigured. Add `websockets>=14,<17` as a direct backend dependency. Update both backend environment examples without adding a real credential.

- [ ] **Step 4: Run focused tests, Ruff and mypy**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/api/test_diagnostics.py -v; .\.venv\Scripts\python.exe -m ruff check app tests/test_config.py tests/api/test_diagnostics.py; .\.venv\Scripts\python.exe -m mypy app`

Expected: all commands PASS.

- [ ] **Step 5: Commit**

```powershell
git add .env.example backend/.env.example backend/app/config.py backend/app/ai/provider.py backend/pyproject.toml backend/tests/test_config.py backend/tests/api/test_diagnostics.py
git commit -m "feat: configure sim1 realtime limits"
```

### Task 2: Contratos JSON de conversación y capacidades

**Files:**
- Create: `backend/app/schemas/conversations.py`
- Modify: `backend/app/schemas/devices.py`
- Modify: `backend/app/devices/protocol.py`
- Test: `backend/tests/devices/test_protocol.py`

**Interfaces:**
- Produces: `DeviceCapability = Literal["audio_pcm16_v1"]`.
- Produces: `ConversationStart`, `ConversationStop` dentro de `DeviceInboundMessage`.
- Produces: `ConversationStarted`, `ConversationStateMessage`, `ConversationTranscript`, `ConversationAudioClear`, `ConversationEnded`, `ConversationError`.
- Produces: `ConversationState`, `ConversationOutcomeValue`, `ConversationErrorCode`, `ConversationStopReason` como tipos literales cerrados de wire; el enum SQLAlchemy se define en Task 4.

- [ ] **Step 1: Write failing strict-schema tests**

```python
def test_conversation_start_is_strict() -> None:
    message = parse_device_message({
        "type": "conversation.start", "boot_id": "boot-a", "seq": 2,
        "version": 1, "mode": "hands_free",
    })
    assert isinstance(message, ConversationStart)

@pytest.mark.parametrize("field", ["prompt", "model", "voice", "home_id", "tools"])
def test_conversation_start_rejects_unauthorized_configuration(field: str) -> None:
    payload = {
        "type": "conversation.start", "boot_id": "boot-a", "seq": 2,
        "version": 1, "mode": "hands_free", field: "forbidden",
    }
    with pytest.raises(ValidationError):
        parse_device_message(payload)
```

Also assert `DeviceHello(capabilities=["audio_pcm16_v1"])` passes, an unknown capability fails, and every outbound model rejects extra fields.

- [ ] **Step 2: Run protocol tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/devices/test_protocol.py -v`

Expected: FAIL because conversation types and hello capabilities are absent.

- [ ] **Step 3: Implement discriminated contracts**

```python
class ConversationStart(StrictMessage):
    type: Literal["conversation.start"]
    version: Literal[1]
    mode: Literal["hands_free"]

class ConversationStop(StrictMessage):
    type: Literal["conversation.stop"]
    version: Literal[1]
    conversation_id: UUID
    reason: Literal["visitor_finished"]
```

Add `capabilities: list[DeviceCapability] = Field(default_factory=list, max_length=8)` to `DeviceHello`. Outbound messages use a separate strict base containing `type`, `version: Literal[1]` and server `seq`; they never require `boot_id`.

`ConversationErrorCode` contains exactly `AI_UNCONFIGURED`, `AI_UNAVAILABLE`, `CONVERSATION_ALREADY_ACTIVE`, `CONVERSATION_NOT_ACTIVE`, `INVALID_AUDIO_FRAME`, `AUDIO_INPUT_OVERFLOW`, `AUDIO_OUTPUT_OVERFLOW`, `STREAM_NOT_FOUND`, `STREAM_NOT_OWNED`, `INVALID_SEQUENCE`, `CONVERSATION_TIMEOUT`, `CONVERSATION_IDLE_TIMEOUT`, and `DEVICE_DISCONNECTED`. `ConversationError` includes that code and a generated `correlation_id`, never internal detail.

- [ ] **Step 4: Run protocol tests and static checks**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/devices/test_protocol.py -v; .\.venv\Scripts\python.exe -m ruff check app/schemas app/devices tests/devices/test_protocol.py; .\.venv\Scripts\python.exe -m mypy app`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/schemas/conversations.py backend/app/schemas/devices.py backend/app/devices/protocol.py backend/tests/devices/test_protocol.py
git commit -m "feat: define sim1 conversation messages"
```

### Task 3: Codec binario PAUD

**Files:**
- Create: `backend/app/devices/audio_protocol.py`
- Create: `backend/tests/devices/test_audio_protocol.py`

**Interfaces:**
- Produces: `AudioDirection(IntEnum)` con `DEVICE_TO_SERVER = 1`, `SERVER_TO_DEVICE = 2`.
- Produces: `AudioFrame(stream_id: UUID, seq: int, direction: AudioDirection, payload: bytes)`.
- Produces: `encode_audio_frame(frame: AudioFrame) -> bytes`.
- Produces: `decode_audio_frame(data: bytes, *, expected_direction: AudioDirection, max_payload_bytes: int = 960) -> AudioFrame`.
- Produces: `AudioFrameError(code: Literal["INVALID_AUDIO_FRAME", "INVALID_SEQUENCE", "STREAM_NOT_FOUND", "STREAM_NOT_OWNED"])`.

- [ ] **Step 1: Write failing round-trip and rejection tests**

```python
def test_audio_frame_round_trip() -> None:
    frame = AudioFrame(UUID("123e4567-e89b-12d3-a456-426614174000"), 1, AudioDirection.DEVICE_TO_SERVER, b"\x00\x00" * 480)
    encoded = encode_audio_frame(frame)
    assert len(encoded) == 34 + 960
    assert decode_audio_frame(encoded, expected_direction=AudioDirection.DEVICE_TO_SERVER) == frame
```

Parameterize mutations for bad magic, version, type, zero/odd/oversized payload, mismatched `payload_length`, sequence zero and wrong direction. Each case must assert the stable public code.

- [ ] **Step 2: Run codec tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/devices/test_audio_protocol.py -v`

Expected: FAIL because the codec module is absent.

- [ ] **Step 3: Implement the exact 34-byte header**

```python
HEADER = Struct(">4sBB16sQI")
MAGIC = b"PAUD"
VERSION = 1

def encode_audio_frame(frame: AudioFrame) -> bytes:
    header = HEADER.pack(MAGIC, VERSION, int(frame.direction), frame.stream_id.bytes, frame.seq, len(frame.payload))
    return header + frame.payload
```

Decode only after confirming the header length, exact remaining length, valid direction, `1 <= seq <= AUDIO_MAX_SEQUENCE`, even non-empty payload and the configured cap. Define `AUDIO_MAX_SEQUENCE = 18_446_744_073_709_551_615`; JSON conserva su límite firmado existente.

- [ ] **Step 4: Run codec tests and static checks**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/devices/test_audio_protocol.py -v; .\.venv\Scripts\python.exe -m ruff check app/devices/audio_protocol.py tests/devices/test_audio_protocol.py; .\.venv\Scripts\python.exe -m mypy app`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/devices/audio_protocol.py backend/tests/devices/test_audio_protocol.py
git commit -m "feat: add paud audio frame codec"
```

### Task 4: Persistencia mínima y exclusión de conversaciones activas

**Files:**
- Create: `backend/alembic/versions/0006_add_sim1_conversation_fields.py`
- Create: `backend/app/services/conversations.py`
- Create: `backend/tests/services/test_conversations.py`
- Modify: `backend/app/models/activity.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `ConversationOutcome` con `REGISTERED`, `DECLINED`, `ABANDONED`, `FAILED`.
- Produces: `create_conversation(db, *, home_id: UUID, device_id: UUID) -> Conversation`.
- Produces: `append_final_transcript(db, *, conversation_id: UUID, role: MessageRole, text: str, provider_item_id: str) -> ConversationMessage | None`.
- Produces: `close_conversation(db, *, conversation_id: UUID, outcome: ConversationOutcome) -> Conversation`.

- [ ] **Step 1: Write failing model and service tests**

```python
async def test_final_transcript_is_idempotent(db, home) -> None:
    device = Device(home_id=home.id, device_id="PI-000001", name="Entrada")
    db.add(device)
    await db.commit()
    conversation = await create_conversation(db, home_id=home.id, device_id=device.id)
    first = await append_final_transcript(db, conversation_id=conversation.id, role=MessageRole.USER, text="Soy Juan", provider_item_id="item-1")
    second = await append_final_transcript(db, conversation_id=conversation.id, role=MessageRole.USER, text="Soy Juan", provider_item_id="item-1")
    assert first is not None
    assert second is None
```

Add a PostgreSQL constraint test proving two `open` conversations for the same non-null `device_id` fail, while historical rows with `device_id=NULL` remain allowed.

- [ ] **Step 2: Run migration/model tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_models.py tests/services/test_conversations.py -v`

Expected: FAIL because fields, enum and service are absent.

- [ ] **Step 3: Add migration, model and transactional service**

The migration creates enum `conversation_outcome`, nullable `device_id` and `outcome`, FK `ON DELETE SET NULL`, plus:

```python
op.create_index(
    "uq_conversations_active_device",
    "conversations",
    ["device_id"],
    unique=True,
    postgresql_where=sa.text("status = 'open' AND device_id IS NOT NULL"),
)
```

Deduplicate final messages by querying `ConversationMessage.metadata_["provider_item_id"].astext` within the single coordinator writer. Normalize text with `.strip()`, reject empty text, cap content at 8,000 characters and store only `{"provider_item_id": value}`.

- [ ] **Step 4: Run migration upgrade/downgrade cycle and tests**

Run: `cd backend; .\.venv\Scripts\python.exe -m alembic upgrade head; .\.venv\Scripts\python.exe -m alembic downgrade 0005_command_lifecycle; .\.venv\Scripts\python.exe -m alembic upgrade head; .\.venv\Scripts\python.exe -m pytest tests/test_models.py tests/services/test_conversations.py -v`

Expected: migration cycle and tests PASS against the guarded test database.

- [ ] **Step 5: Commit**

```powershell
git add backend/alembic/versions/0006_add_sim1_conversation_fields.py backend/app/models/activity.py backend/app/models/__init__.py backend/app/services/conversations.py backend/tests/test_models.py backend/tests/services/test_conversations.py
git commit -m "feat: persist sim1 conversations"
```

### Task 5: Contrato interno y proveedor Realtime falso

**Files:**
- Create: `backend/app/ai/realtime.py`
- Create: `backend/app/ai/fake_realtime.py`
- Create: `backend/tests/ai/test_realtime_provider.py`

**Interfaces:**
- Produces: `RealtimeSessionConfig(model: str, instructions: str, voice: str, language: str)`.
- Produces eventos `SessionReady`, `SpeechStarted`, `SpeechStopped`, `ResponseStarted`, `AudioDelta`, `TranscriptFinal`, `ResponseFinished`, `ProviderFailure`.
- Produces: `AIRealtimeSession.send_audio(pcm16: bytes)`, `events()`, `cancel_response()`, `close()`.
- Produces: `AIRealtimeProvider.diagnose() -> ProviderStatus` y async `open_session(config: RealtimeSessionConfig) -> AIRealtimeSession`.
- Produces: `FakeRealtimeProvider` con `sessions`, `fail_open` y sesiones alimentables desde tests.

- [ ] **Step 1: Write failing provider-contract tests**

```python
async def test_fake_session_records_audio_and_emits_events() -> None:
    provider = FakeRealtimeProvider()
    session = await provider.open_session(RealtimeSessionConfig("gpt-realtime-2.1", "Sé breve", "default", "es-AR"))
    await session.send_audio(b"\x00\x00" * 480)
    await session.emit(TranscriptFinal(role=MessageRole.USER, text="Hola", item_id="item-1"))
    event = await anext(session.events())
    assert session.received_audio == [b"\x00\x00" * 480]
    assert event == TranscriptFinal(role=MessageRole.USER, text="Hola", item_id="item-1")
```

- [ ] **Step 2: Run provider tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_realtime_provider.py -v`

Expected: FAIL because the internal contract and fake do not exist.

- [ ] **Step 3: Implement a transport-neutral async protocol and deterministic fake**

Use frozen slot dataclasses for config/events. `events()` returns one async iterator per session; `close()` places a private sentinel, is idempotent, and causes the iterator to finish. `cancel_response()` increments `cancel_count`. `FakeRealtimeProvider.diagnose()` returns `AVAILABLE` salvo que `fail_open` esté activo. Neither fake nor event dataclasses accept external provider dictionaries.

- [ ] **Step 4: Run provider tests and static checks**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_realtime_provider.py -v; .\.venv\Scripts\python.exe -m ruff check app/ai tests/ai; .\.venv\Scripts\python.exe -m mypy app`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/ai/realtime.py backend/app/ai/fake_realtime.py backend/tests/ai/test_realtime_provider.py
git commit -m "feat: define realtime provider contract"
```

### Task 6: Adaptador OpenAI Realtime real

**Files:**
- Create: `backend/app/ai/openai_realtime.py`
- Modify: `backend/app/ai/provider.py`
- Modify: `backend/tests/ai/test_realtime_provider.py`

**Interfaces:**
- Consumes: contratos de Task 5 y `Settings` de Task 1.
- Produces: `OpenAIRealtimeProvider.open_session(config) -> AIRealtimeSession`.
- Produces: `OpenAIRealtimeSession` que sólo expone eventos internos.

- [ ] **Step 1: Add failing mocked-transport tests**

Use a `ScriptedRealtimeTransport` que capture JSON enviado y devuelva una secuencia finita. Assert:

```python
assert transport.sent[0]["type"] == "session.update"
assert transport.sent[0]["session"]["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
assert transport.sent[0]["session"]["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
assert transport.sent[0]["session"]["audio"]["input"]["turn_detection"]["type"] == "server_vad"
```

Also assert `session.created`/`session.updated` becomes `SessionReady`, PCM bytes become one base64 `input_audio_buffer.append`, `response.output_audio.delta` becomes `AudioDelta`, both final transcript event families become `TranscriptFinal`, `response.cancel` is emitted once, external errors become sanitized `ProviderFailure`, and the API key never appears in exceptions or `repr`.

- [ ] **Step 2: Run provider tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_realtime_provider.py -v`

Expected: FAIL because the real adapter is absent.

- [ ] **Step 3: Implement transport and event normalization**

Connect to `wss://api.openai.com/v1/realtime?model=<urlencoded-model>` using an `Authorization: Bearer` header sourced from `SecretStr`. Configure audio PCM 24 kHz, server VAD with automatic response creation, input transcription in the configured language, audio output and the merged instructions. `open_session` does not return until `session.updated` confirms configuration or the bounded open timeout fails; it also queues one `SessionReady` internal event. Map only documented event types into Task 5 dataclasses; ignore unknown informational events and convert `error` into `ProviderFailure(code="AI_UNAVAILABLE")` without raw payload.

Resolve model as `settings.openai_realtime_model` when `AgentConfig.realtime_model == "env-default"`; otherwise use the persisted model. Do not declare tools in SIM-1. The provider diagnosis begins `configured`, changes to `available` after a confirmed open, and changes to `unavailable` after a sanitized connection/provider failure; an absent or placeholder key remains `unconfigured`.

- [ ] **Step 4: Run mocked provider tests, Ruff and mypy**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_realtime_provider.py -v; .\.venv\Scripts\python.exe -m ruff check app/ai tests/ai; .\.venv\Scripts\python.exe -m mypy app`

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/ai/openai_realtime.py backend/app/ai/provider.py backend/tests/ai/test_realtime_provider.py
git commit -m "feat: adapt openai realtime websocket"
```

### Task 7: Coordinador — inicio, ownership y persistencia

**Files:**
- Create: `backend/app/ai/conversation_coordinator.py`
- Create: `backend/tests/ai/test_conversation_coordinator.py`

**Interfaces:**
- Consumes: provider Task 5, services Task 4, messages Task 2.
- Produces: `ConversationTransport.send_control(message) -> Awaitable[None]`, `send_audio(data: bytes) -> Awaitable[None]`.
- Produces: `ConversationCoordinator.start(device_id: UUID, ownership: DeviceConnectionLease, transport: ConversationTransport) -> None`.
- Produces: `stop(device_id: UUID, ownership: DeviceConnectionLease, reason: ConversationStopReason) -> None`.
- Produces: `disconnect(device_id: UUID, ownership: DeviceConnectionLease) -> None` y `shutdown() -> None`.

- [ ] **Step 1: Write failing lifecycle tests**

Cover the exact order: validate enabled `AgentConfig` and key, reserve device, open fake provider, create durable conversation, allocate a different `stream_id`, then send `conversation.started`. Assert `fail_open=True` yields `AI_UNAVAILABLE` and zero rows; missing/placeholder key yields `AI_UNCONFIGURED` and zero rows; a second start yields `CONVERSATION_ALREADY_ACTIVE`; wrong lease cannot stop; disconnect closes as `abandoned`; manual stop closes as `registered`.

```python
assert transport.controls[0].type == "conversation.started"
assert transport.controls[0].conversation_id == conversation.id
assert transport.controls[0].stream_id != conversation.id
```

- [ ] **Step 2: Run coordinator tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_conversation_coordinator.py -v`

Expected: FAIL because the coordinator is absent.

- [ ] **Step 3: Implement atomic reservation and short database sessions**

Construct the coordinator with `async_sessionmaker[AsyncSession]`, provider, settings and registry. Guard `_active: dict[UUID, ActiveConversation]` with one `asyncio.Lock`. Reserve before external I/O; on every failure remove the reservation. Read `Device` and `AgentConfig` in a short session, build non-overridable portero rules plus the home prompt, open provider, then create/commit the conversation in a fresh session. If persistence fails, close provider before releasing the reservation.

- [ ] **Step 4: Run lifecycle tests and static checks**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_conversation_coordinator.py tests/services/test_conversations.py -v; .\.venv\Scripts\python.exe -m ruff check app/ai/conversation_coordinator.py tests/ai/test_conversation_coordinator.py; .\.venv\Scripts\python.exe -m mypy app`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/ai/conversation_coordinator.py backend/tests/ai/test_conversation_coordinator.py
git commit -m "feat: coordinate sim1 conversation lifecycle"
```

### Task 8: Coordinador — audio, VAD, barge-in, límites y cleanup

**Files:**
- Modify: `backend/app/ai/conversation_coordinator.py`
- Modify: `backend/tests/ai/test_conversation_coordinator.py`

**Interfaces:**
- Produces: `receive_audio(device_id: UUID, ownership: DeviceConnectionLease, frame: AudioFrame) -> None`.
- Maintains: input `asyncio.Queue[bytes]`, output `asyncio.Queue[bytes]`, input/output sequence and `last_activity` por conversación.

- [ ] **Step 1: Write failing audio lifecycle tests**

Test forwarding of input PCM; packetization of arbitrary provider deltas into 960-byte type-2 frames; final transcript control plus durable dedupe; VAD state changes; barge-in cancel + output drain + `conversation.audio.clear`; input overflow; output overflow fail-fast; max timeout; idle timeout; idempotent stop; and zero live tasks after every terminal path.

```python
await fake_session.emit(SpeechStarted())
assert fake_session.cancel_count == 1
assert any(message.type == "conversation.audio.clear" for message in transport.controls)
assert transport.pending_playback == []
```

- [ ] **Step 2: Run coordinator tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_conversation_coordinator.py -v`

Expected: FAIL on audio and timeout cases.

- [ ] **Step 3: Implement bounded pumps and one terminal cleanup path**

Create named tasks for input pump, provider events, output sender, maximum timer and idle timer. Only provider speech/response events reset semantic inactivity; silent PCM packets do not. On `SpeechStarted` during assistant output: call `cancel_response`, drain output queue, reset packetizer, send `conversation.audio.clear(reason="barge_in")`, then state `visitor_speaking`. On either `QueueFull`, call the same terminal method with `AUDIO_INPUT_OVERFLOW` or `AUDIO_OUTPUT_OVERFLOW`. The terminal method runs once under an active-session lock, cancels sibling tasks, awaits them with `return_exceptions=True`, closes provider, persists outcome and sends `conversation.ended` when transport remains owned.

- [ ] **Step 4: Run coordinator tests repeatedly**

Run: `cd backend; 1..3 | ForEach-Object { .\.venv\Scripts\python.exe -m pytest tests/ai/test_conversation_coordinator.py -q }; .\.venv\Scripts\python.exe -m ruff check app/ai tests/ai; .\.venv\Scripts\python.exe -m mypy app`

Expected: three deterministic PASS runs and clean static checks.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/ai/conversation_coordinator.py backend/tests/ai/test_conversation_coordinator.py
git commit -m "feat: stream bounded realtime audio"
```

### Task 9: Integración con el WebSocket de dispositivo

**Files:**
- Modify: `backend/app/devices/connections.py`
- Modify: `backend/app/websocket/device.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/devices/test_connections.py`
- Modify: `backend/tests/websocket/test_device_socket.py`

**Interfaces:**
- Consumes: coordinator Task 7/8, JSON Task 2, codec Task 3.
- Produces: recepción discriminada de `str | bytes` después de auth.
- Produces: emisor serializado de controles con `version=1` y `seq` creciente.

- [ ] **Step 1: Extend the scripted socket and write failing integration tests**

Add `send_bytes` and binary inbound support to `ScriptedWebSocket`. Assert binary before auth still closes `4000`; binary without capability yields public `INVALID_AUDIO_FRAME` and closes only a started conversation; wrong stream and non-increasing binary sequence are rejected; valid start/audio/stop reaches the fake provider; socket replacement and disconnect call coordinator cleanup exactly once; legacy clients without capabilities still handle heartbeat and commands unchanged.

- [ ] **Step 2: Run WebSocket and registry tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/devices/test_connections.py tests/websocket/test_device_socket.py -v`

Expected: new integration cases FAIL while legacy cases remain green.

- [ ] **Step 3: Add binary transport without changing the handshake**

Replace `_receive_text` only in the authenticated loop with `_receive_frame() -> str | bytes`; keep both handshake reads text-only. Store hello capabilities beside the lease. Text messages retain the existing `boot_id`/`seq` checks; binary frames use the PAUD sequence tracked by the active conversation. Add `send_bytes` to `DeviceSocket` and registry. Build the coordinator in `create_app` from the existing session factory/provider/registry, pass it to the route through `websocket.app.state`, and call `shutdown()` before `device_connections.close_all()`.

- [ ] **Step 4: Run the complete backend suite and quality gates**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest -v; .\.venv\Scripts\python.exe -m ruff check .; .\.venv\Scripts\python.exe -m ruff format --check .; .\.venv\Scripts\python.exe -m mypy app`

Expected: all backend tests and checks PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/devices/connections.py backend/app/websocket/device.py backend/app/main.py backend/tests/devices/test_connections.py backend/tests/websocket/test_device_socket.py
git commit -m "feat: carry realtime audio over device websocket"
```

### Task 10: Cliente de dispositivo web y codecs TypeScript

**Files:**
- Create: `frontend/lib/simulator/contracts.ts`
- Create: `frontend/lib/simulator/audio-frame.ts`
- Create: `frontend/lib/simulator/audio-codec.ts`
- Create: `frontend/lib/simulator/device-client.ts`
- Create: `frontend/tests/simulator-protocol.test.ts`
- Modify: `frontend/.env.example`

**Interfaces:**
- Produces: `encodeAudioFrame`, `decodeAudioFrame`, `resampleTo24k`, `floatToPcm16`.
- Produces: `PorteroDeviceClient.connect({deviceId, secret})`, `startConversation()`, `sendAudio(pcm16)`, `stopConversation()`, `disconnect()`.
- Produces callbacks `onState`, `onTranscript`, `onAudio`, `onAudioClear`, `onEnded`, `onError`.

- [ ] **Step 1: Write failing browser protocol tests**

Assert byte-for-byte HMAC canonicalization `v1\nPI-000001\nboot-a\nnonce`, hello capability, post-auth device status (`camera=unavailable`, `microphone=ready`, `speaker=ready`), monotonically increasing JSON sequence, exact 34-byte PAUD header, separate input sequence, strict stream matching, heartbeat cleanup and no writes to cookies/storage/URL.

```typescript
expect(Array.from(new Uint8Array(encoded.slice(0, 4)))).toEqual([80, 65, 85, 68]);
expect(decoded.streamId).toBe("123e4567-e89b-12d3-a456-426614174000");
expect(decoded.sequence).toBe(1n);
```

- [ ] **Step 2: Run frontend protocol tests and verify failure**

Run: `cd frontend; npm test -- --run tests/simulator-protocol.test.ts`

Expected: FAIL because simulator libraries are absent.

- [ ] **Step 3: Implement strict parsing, Web Crypto HMAC and WebSocket client**

Use `crypto.subtle.importKey`/`sign`, lowercase hex, `WebSocket.binaryType="arraybuffer"`, one boot UUID per page lifetime and `NEXT_PUBLIC_DEVICE_WS_URL` defaulting to `ws://localhost:8000/ws/device` only in development. After `auth.ok`, send one `device.status` with simulator firmware/hardware labels, camera unavailable and audio ready, then start heartbeat at the server interval. Hold the secret only in the `connect` call closure; overwrite the component state immediately after handoff and clear the client reference on auth failure/disconnect. Reject malformed server JSON before invoking callbacks.

- [ ] **Step 4: Run protocol tests, lint and typecheck**

Run: `cd frontend; npm test -- --run tests/simulator-protocol.test.ts; npm run lint; npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/.env.example frontend/lib/simulator/contracts.ts frontend/lib/simulator/audio-frame.ts frontend/lib/simulator/audio-codec.ts frontend/lib/simulator/device-client.ts frontend/tests/simulator-protocol.test.ts
git commit -m "feat: add browser device protocol client"
```

### Task 11: AudioWorklet, micrófono y reproducción con clear real

**Files:**
- Create: `frontend/public/portero-audio-worklet.js`
- Create: `frontend/lib/simulator/audio-session.ts`
- Create: `frontend/tests/simulator-audio.test.ts`
- Modify: `frontend/tests/setup.ts`

**Interfaces:**
- Consumes: `resampleTo24k` y `floatToPcm16` de Task 10.
- Produces: `PorteroAudioSession.start(onFrame, onLevel)`, `enqueuePlayback(pcm16)`, `clearPlayback()`, `close()`.

- [ ] **Step 1: Write failing DSP and lifecycle tests**

Test 48 kHz -> 24 kHz sample count, saturation to signed PCM16, exact 480-sample frame aggregation, missing `mediaDevices`, denied permission, worklet registration, playback enqueue, `clearPlayback()` posting `{type:"clear"}`, and `close()` stopping every track and closing AudioContext once.

- [ ] **Step 2: Run audio tests and verify failure**

Run: `cd frontend; npm test -- --run tests/simulator-audio.test.ts`

Expected: FAIL because the audio session/worklet are absent.

- [ ] **Step 3: Implement capture and playback worklet**

The worklet processor accepts `{type:"play", samples: Float32Array}` and `{type:"clear"}`. Its `process()` copies queued playback into output, writes zero when empty, computes capture RMS, and posts capture blocks to the main thread. `PorteroAudioSession` requests `{audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}}`, resamples from `audioContext.sampleRate`, emits exactly 480 input samples per callback, and converts server PCM16 back to Float32 before `play`.

- [ ] **Step 4: Run audio tests, lint and typecheck**

Run: `cd frontend; npm test -- --run tests/simulator-audio.test.ts; npm run lint; npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/public/portero-audio-worklet.js frontend/lib/simulator/audio-session.ts frontend/tests/simulator-audio.test.ts frontend/tests/setup.ts
git commit -m "feat: add hands free browser audio"
```

### Task 12: Pantalla independiente del simulador

**Files:**
- Create: `frontend/app/simulador-portero/page.tsx`
- Create: `frontend/components/simulator/portero-simulator.tsx`
- Create: `frontend/tests/simulator-page.test.tsx`
- Modify: `frontend/app/globals.css`

**Interfaces:**
- Consumes: `PorteroDeviceClient` Task 10 y `PorteroAudioSession` Task 11.
- Produces: ruta pública de dispositivo `/simulador-portero`, fuera de `(panel)`.

- [ ] **Step 1: Write failing accessible-flow tests**

Test validación `^PI-[0-9]{6}$`, secreto requerido, auth exitosa, borrado inmediato del input secreto, `Tocar timbre` as the only microphone trigger, disabled/enabled states, Spanish labels for all five states, live transcript by role, meter semantics, audio clear delegation, finish cleanup, retry after denied permission and unmount cleanup.

```typescript
expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled();
expect(screen.getByLabelText("Secreto del dispositivo")).toHaveValue("");
expect(screen.getByRole("status")).toHaveTextContent("Escuchando");
```

- [ ] **Step 2: Run page tests and verify failure**

Run: `cd frontend; npm test -- --run tests/simulator-page.test.tsx`

Expected: FAIL because the route and component are absent.

- [ ] **Step 3: Implement the finite UI flow and isolated styling**

Use phases `disconnected`, `connecting`, `connected`, `preparing`, `listening`, `visitor_speaking`, `assistant_speaking`, `error`. Keep transcript entries as `{id, role, text}` and render only final messages from the backend. Use `aria-live="polite"` for state/transcripts, an accessible `<meter>` for mic level, 44 px minimum controls, visible focus, one-column mobile layout and no administrative sidebar/session requirement. Do not add camera controls in SIM-1.

- [ ] **Step 4: Run all frontend tests and production checks**

Run: `cd frontend; npm test -- --run; npm run lint; npm run typecheck; npm run build`

Expected: tests, lint, types and 14-page production build PASS, including `/simulador-portero`.

- [ ] **Step 5: Commit**

```powershell
git add frontend/app/simulador-portero/page.tsx frontend/components/simulator/portero-simulator.tsx frontend/tests/simulator-page.test.tsx frontend/app/globals.css
git commit -m "feat: add independent portero simulator"
```

### Task 13: Smoke opt-in, protocolo, runbook y aceptación SIM-1

**Files:**
- Create: `backend/app/ai/smoke.py`
- Create: `backend/tests/ai/test_smoke.py`
- Modify: `docs/device-protocol-v1.md`
- Modify: `docs/architecture.md`
- Modify: `README.md`
- Modify: `backend/tests/test_documentation.py`

**Interfaces:**
- Produces: `python -m app.ai.smoke`, que se niega a llamar OpenAI salvo flag explícito.

- [ ] **Step 1: Write failing smoke guard and documentation tests**

```python
def test_smoke_refuses_without_opt_in(settings, capsys) -> None:
    settings.openai_realtime_smoke = False
    assert run_smoke(settings) == 2
    assert "OPENAI_REALTIME_SMOKE=true" in capsys.readouterr().err
```

Assert docs contain `PAUD`, `conversation.audio.clear`, distinct ID definitions, both overflow codes, no browser-to-OpenAI path, model `gpt-realtime-2.1` and the SIM-1 manual acceptance steps.

- [ ] **Step 2: Run smoke/documentation tests and verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/ai/test_smoke.py tests/test_documentation.py -v`

Expected: FAIL because the command and active protocol docs are absent.

- [ ] **Step 3: Implement guarded smoke and update runbook**

The smoke uses the real provider for one short session, waits for normalized `SessionReady`, sends at most one second of zero PCM, always closes in `finally`, and prints only stable status/correlation data. Opening and readiness share a 10-second timeout. README documents setting the key with PowerShell `Read-Host -AsSecureString`, never includes a real value, and states that the smoke can incur API usage.

- [ ] **Step 4: Run repository verification**

Run from repo root with the guarded `TEST_DATABASE_URL` already configured: `.\scripts\verify.ps1`

Expected: migrations, backend tests, simulator CLI tests, frontend tests, Ruff, formatting, mypy, ESLint, TypeScript and production build all PASS. The real OpenAI smoke does not run.

- [ ] **Step 5: Perform manual SIM-1 acceptance**

Start PostgreSQL, backend with a locally configured real API key, and frontend. Provision or rotate `PI-000001`; open `/simulador-portero` in a clean browser profile; authenticate; press `Tocar timbre`; record the distinct `conversation_id` and `stream_id` from `conversation.started`; say “Hola, soy Juan, vengo a entregar un paquete”; verify bidirectional audio; interrupt the agent while speaking; verify immediate stop; continue; press `Finalizar visita`; then query PostgreSQL read-only to confirm one closed conversation, final user/assistant messages, no audio columns and no duplicate messages.

- [ ] **Step 6: Run the opt-in smoke once**

Set `OPENAI_REALTIME_SMOKE=true` only for this command, run `cd backend; .\.venv\Scripts\python.exe -m app.ai.smoke`, then clear the process variable. Expected: one bounded session opens, yields a normalized event and closes without printing the key.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/ai/smoke.py backend/tests/ai/test_smoke.py backend/tests/test_documentation.py docs/device-protocol-v1.md docs/architecture.md README.md
git commit -m "docs: verify and operate sim1 voice flow"
```

---

## SIM-1 Completion Gate

Do not begin SIM-2 after merely finishing Task 13. Record the evidence from `scripts/verify.ps1`, the opt-in smoke and the manual browser test, then compare it line-by-line with section 20 of the spec. SIM-2 starts only after the user explicitly accepts that evidence.
