#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "portero_codec.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WS_TRANSPORT_EVENT_ONLINE,       /* auth.ok received — device is ONLINE */
    WS_TRANSPORT_EVENT_DISCONNECTED, /* connection lost (auto-reconnect pending) */
    WS_TRANSPORT_EVENT_COMMAND,      /* command.request received from backend */
} ws_transport_event_type_t;

typedef struct {
    ws_transport_event_type_t type;
    union {
        struct { uint32_t heartbeat_interval_s; } online;
        portero_command_request_t command;
    };
} ws_transport_event_t;

typedef void (*ws_transport_cb_t)(const ws_transport_event_t *ev, void *ctx);

/**
 * Connect to the backend WebSocket URL and begin the auth handshake.
 * secret is copied internally and zeroized on ws_transport_stop().
 * Returns ESP_ERR_INVALID_STATE if already started.
 */
esp_err_t ws_transport_start(const char *url,
                              const char *device_id,
                              const char *secret,
                              ws_transport_cb_t cb,
                              void *ctx);

/** Stop the WebSocket connection and zeroize the stored secret. */
esp_err_t ws_transport_stop(void);

/** Returns true after auth.ok is received and until the next disconnect. */
bool ws_transport_is_online(void);

#ifdef __cplusplus
}
#endif
