#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "portero_codec.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WS_TRANSPORT_EVENT_ONLINE,                   /* auth.ok received — device is ONLINE */
    WS_TRANSPORT_EVENT_DISCONNECTED,             /* connection lost (auto-reconnect pending) */
    WS_TRANSPORT_EVENT_COMMAND,                  /* command.request received from backend */
    WS_TRANSPORT_EVENT_CONVERSATION_STARTED,     /* conversation.started received (backend ack) */
    WS_TRANSPORT_EVENT_CONVERSATION_ENDED,       /* conversation.ended received (backend terminated) */
    WS_TRANSPORT_EVENT_CONVERSATION_AUDIO_CLEAR, /* conversation.audio.clear received */
    WS_TRANSPORT_EVENT_CONVERSATION_ERROR,       /* conversation.error received (backend rejected/aborted) */
} ws_transport_event_type_t;

typedef struct {
    ws_transport_event_type_t type;
    union {
        struct { uint32_t heartbeat_interval_s; } online;
        portero_command_request_t              command;
        portero_conversation_started_t         conversation_started;
        portero_conversation_ended_t           conversation_ended;
        portero_conversation_audio_clear_t     conversation_audio_clear;
        portero_conversation_error_t           conversation_error;
    };
} ws_transport_event_t;

typedef void (*ws_transport_cb_t)(const ws_transport_event_t *ev, void *ctx);

/**
 * Callback invoked (from WebSocket task) for each complete incoming binary frame.
 * data points into an internal static buffer valid only for the duration of the call.
 */
typedef void (*ws_transport_binary_rx_cb_t)(const uint8_t *data, size_t len, void *ctx);

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

/**
 * Send command.result to the backend. Must be called after receiving
 * WS_TRANSPORT_EVENT_COMMAND. result_json is a JSON object string (e.g. "{}").
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_command_result(const char *command_id,
                                            portero_result_status_t status,
                                            portero_device_error_code_t error_code,
                                            const char *result_json);

/**
 * Send conversation.start to backend (device initiates conversation).
 * Backend responds with conversation.started (WS_TRANSPORT_EVENT_CONVERSATION_STARTED).
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_conversation_start(void);

/**
 * Send conversation.stop to backend (device initiates termination).
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_conversation_stop(const char *conversation_id);

/**
 * Inform ws_transport of the current peripheral status (mic/speaker).
 * Call after board_audio_init() to make device.status report them as ready.
 */
void ws_transport_set_peripheral_status(portero_peripheral_status_t mic,
                                        portero_peripheral_status_t spk);

/**
 * Register a callback for incoming binary WebSocket frames (PAUD audio from backend).
 * Safe to call before or after ws_transport_start(). ctx is passed through unchanged.
 * The callback runs in the WebSocket task — keep it short (post to a queue, do not block).
 */
esp_err_t ws_transport_set_binary_rx_cb(ws_transport_binary_rx_cb_t cb, void *ctx);

/**
 * Send one binary WebSocket frame (a 994-byte PAUD audio frame).
 * Non-blocking with a 20 ms timeout — drops the frame and returns ESP_ERR_TIMEOUT
 * if the WS TX path is busy. Callers must tolerate drops (audio is real-time).
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_audio_frame(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif
