"""Executable checks for the public MVP1 documentation contract."""

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_protocol_document_contains_complete_v1_contract() -> None:
    text = (_repo_root() / "docs/device-protocol-v1.md").read_text(encoding="utf-8")

    assert "v1\\n{device_id}\\n{boot_id}\\n{nonce}" in text
    assert "UTF-8" in text
    assert "hexadecimal minúsculo" in text
    for message in (
        "device.hello",
        "auth.challenge",
        "auth.response",
        "auth.ok",
        "device.heartbeat",
        "device.status",
        "command.request",
        "command.ack",
        "command.result",
    ):
        assert message in text
    for lifecycle in (
        "pending -> sent -> acknowledged -> completed",
        "pending -> failed",
        "sent -> failed | timeout",
        "acknowledged -> failed | timeout | completed",
    ):
        assert lifecycle in text
    for contract in (
        "AUTH_FAILED",
        "PROTOCOL_VERSION_UNSUPPORTED",
        "MAX_WEBSOCKET_MESSAGE_BYTES",
        "MAX_UPLOAD_BYTES",
        "image/jpeg",
        "1, 2, 4, 8, 15",
        "código 4000",
        "código 4500",
        "4001 / replaced",
        "1001 / shutdown",
        "400 INVALID_MEDIA",
        "SIM-2 — no implementado",
        "stream_id",
        "payload_length",
    ):
        assert contract in text
    for sim1_contract in (
        "PAUD",
        "conversation.audio.clear",
        "conversation_id es durable",
        "stream_id es ef\u00edmero",
        "AUDIO_INPUT_OVERFLOW",
        "AUDIO_OUTPUT_OVERFLOW",
        "audio_pcm16_v1",
        "El navegador nunca se conecta directamente a OpenAI",
    ):
        assert sim1_contract in text


def test_readme_contains_windows_runbook_and_all_acceptance_flows() -> None:
    text = (_repo_root() / "README.md").read_text(encoding="utf-8")

    for command_or_port in (
        "docker compose up -d db",
        "alembic upgrade head",
        "python -m app.bootstrap",
        "uvicorn app.main:app --reload",
        "uvicorn app.main:app --workers 1",
        "--loop app.event_loop:selector_loop_factory",
        "npm run dev",
        "portero-device-simulator",
        "http://localhost:3000",
        "http://localhost:8000",
        "localhost:5432",
        "scripts\\verify.ps1",
    ):
        assert command_or_port in text
    for acceptance_flow in (
        "bootstrap",
        "Dashboard",
        "Agente IA",
        "PI-000001",
        "ONLINE",
        "OFFLINE",
        "camera.capture",
        "ACK",
        "RESULT",
        "JPEG",
        "eventos",
        "estadísticas",
        "diagnóstico",
        "auditoría",
    ):
        assert acceptance_flow in text
    assert "un solo proceso efectivo" in text
    assert "coordinador compartido" in text
    assert "current_database()" in text
    assert "`dbname`" in text
    assert "`service`" in text
    for sim1_runbook_item in (
        "http://localhost:3000/simulador-portero",
        "python.exe -m app.ai.smoke",
        "OPENAI_REALTIME_SMOKE=true",
        "gpt-realtime-2.1",
        "Read-Host 'OpenAI API key' -AsSecureString",
        "Tocar timbre",
        "Finalizar visita",
        "Hola, soy Juan, vengo a entregar un paquete",
        "conversation_id",
        "stream_id",
        "barge-in",
        "transcripciones finales",
        "no existen columnas de audio",
        "puede generar consumo facturable",
        "El navegador nunca recibe `OPENAI_API_KEY`",
        "BEGIN TRANSACTION READ ONLY;",
        "$sim1ReadOnlySql | docker compose exec -T db psql -X",
        '-v "conversation_id=$conversationId"',
    ):
        assert sim1_runbook_item in text
    assert "defina exactamente `OPENAI_REALTIME_SMOKE=true`" not in text
    assert '-c "SELECT c.id' not in text


def test_architecture_documents_active_sim1_realtime_path() -> None:
    text = (_repo_root() / "docs/architecture.md").read_text(encoding="utf-8")

    for contract in (
        "PAUD",
        "conversation.audio.clear",
        "AUDIO_INPUT_OVERFLOW",
        "AUDIO_OUTPUT_OVERFLOW",
        "gpt-realtime-2.1",
        "conversation_id",
        "stream_id",
        "FakeRealtimeProvider",
        "OpenAIRealtimeProvider",
        "El navegador nunca se conecta directamente a OpenAI",
    ):
        assert contract in text
