# Portero firmware foundation

The physical-device application is rooted at `firmware/esp32-p4`. It targets
ESP32-P4 with the FW-1 board-safe defaults and resolves the shared,
hardware-neutral components through `EXTRA_COMPONENT_DIRS`. It intentionally
does not configure Ethernet, authentication, codec, transport, or supervision
behavior yet.

This is a hardware-neutral ESP-IDF foundation for Protocol V1.  It owns the
connection state (`BOOT -> NETWORK_CONNECTING -> BACKEND_CONNECTING ->
AUTHENTICATING -> IDLE`), per-boot `boot_id`, monotonically increasing `seq`,
reconnect backoff, and board-independent control-message primitives. Transport
and physical drivers are deliberately outside this layer.

## Hardware boundary

No GPIO, PHY, I2S, display, camera, or Ethernet selection is present. The
`hardware_hal` component reports no enabled capabilities and its camera, audio,
and Ethernet start operations return `ESP_ERR_NOT_SUPPORTED` until a concrete
board implementation replaces these stubs.

Before selecting or copying a board configuration, confirm all of the
following: board model, silicon revision, external PHY model/address, and the
board schematic. The manufacturer examples are implementation references only:

- `C:\PorteroIA\ESP32-P4-Platform\examples\esp-idf\11_ethernetbasic`
- `C:\PorteroIA\ESP32-P4-Platform\examples\esp-idf\12_I2SCodec`
- `C:\PorteroIA\ESP32-P4-Platform\examples\esp-idf\16_video_lcd_display`
- `C:\PorteroIA\ESP32-P4-Platform\examples\esp-idf\17_simple_video_server`

This FW-1 firmware migration neither copies nor modifies files in the
manufacturer repository; it does not make a claim about the external
repository's pre-existing working-tree state. Their hardware choices must be
reviewed against the selected board before use.

## Build versus executed tests

After installing and activating the project-compatible ESP-IDF toolchain, a
host can choose its confirmed target and compile the firmware:

```powershell
cd firmware/esp32-p4
idf.py set-target esp32p4
idf.py build
```

`firmware/test_apps/device_protocol` is an independent Unity application. A
successful `idf.py build` only proves compilation; it does **not** execute the
Unity suite. With a selected test board, build, flash, and monitor it
separately:

```powershell
cd firmware/test_apps/device_protocol
idf.py build
idf.py flash
idf.py monitor
```

Record the Unity console output from `monitor` as the evidence that the tests
actually ran.
