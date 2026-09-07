#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DEVICE_PROTOCOL_BOOT_ID_MAX_LENGTH 128U
#define DEVICE_PROTOCOL_BOOT_ID_BUFFER_SIZE (DEVICE_PROTOCOL_BOOT_ID_MAX_LENGTH + 1U)

typedef enum {
    DEVICE_CONNECTION_INVALID = -1,
    DEVICE_CONNECTION_BOOT = 0,
    DEVICE_CONNECTION_PROVISIONING,
    DEVICE_CONNECTION_NETWORK_CONNECTING,
    DEVICE_CONNECTION_BACKEND_CONNECTING,
    DEVICE_CONNECTION_AUTHENTICATING,
    DEVICE_CONNECTION_ONLINE,
    DEVICE_CONNECTION_STATE_COUNT,
} device_connection_state_t;

typedef struct {
    device_connection_state_t state;
    char boot_id[DEVICE_PROTOCOL_BOOT_ID_BUFFER_SIZE];
    uint64_t next_seq;
    uint32_t reconnect_backoff_ms;
} device_protocol_context_t;

/** Initializes connection state and protocol-owned session metadata. */
esp_err_t device_protocol_init(device_protocol_context_t *context,
                               const char *boot_id,
                               uint32_t reconnect_backoff_ms);

/** Returns the current connection state without consulting hardware. */
device_connection_state_t device_protocol_get_state(const device_protocol_context_t *context);

/** Performs one allowed connection-state transition. */
esp_err_t device_protocol_transition(device_protocol_context_t *context,
                                     device_connection_state_t next_state);

/** Transitions a booted device without valid configuration to provisioning. */
esp_err_t device_protocol_transition_to_missing_config(device_protocol_context_t *context);

/** Returns the fixed sequence number used by every socket's device.hello. */
uint64_t device_protocol_hello_sequence(const device_protocol_context_t *context);

/** Allocates the next strictly increasing non-hello sequence for this boot. */
esp_err_t device_protocol_next_sequence(device_protocol_context_t *context, uint64_t *sequence);

/** Updates transport retry policy without involving a network driver. */
esp_err_t device_protocol_set_reconnect_backoff(device_protocol_context_t *context,
                                                uint32_t reconnect_backoff_ms);

#ifdef __cplusplus
}
#endif
