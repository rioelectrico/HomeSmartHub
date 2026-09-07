"""Protocol V1 parsing entry points."""

from collections.abc import Mapping

from pydantic import TypeAdapter

from app.schemas.devices import DeviceInboundMessage

_message_adapter: TypeAdapter[DeviceInboundMessage] = TypeAdapter(DeviceInboundMessage)


def parse_device_message(payload: str | bytes | Mapping[str, object]) -> DeviceInboundMessage:
    """Parse JSON or an already-decoded mapping into one strict V1 message."""

    if isinstance(payload, (str, bytes)):
        return _message_adapter.validate_json(payload)
    return _message_adapter.validate_python(payload)
