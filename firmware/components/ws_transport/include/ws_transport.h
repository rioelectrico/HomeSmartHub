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
    WS_TRANSPORT_EVENT_CONVERSATION_START,       /* conversation.start received */
    WS_TRANSPORT_EVENT_CONVERSATION_STOP,        /* conversation.stop received */
    WS_TRANSPORT_EVENT_CONVERSATION_AUDIO_CLEAR, /* conversation.audio.clear received */
} ws_transport_event_type_t;

typedef struct {
    ws_transport_event_type_t type;
    union {
        struct { uint32_t heartbeat_interval_s; } online;
        portero_command_request_t              command;
        portero_conversation_start_t           conversation_start;
        portero_conversation_stop_t            conversation_stop;
        portero_conversation_audio_clear_t     conversation_audio_clear;
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
 * Send device.ring to backend (doorbell pressed).
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_ring(void);

/**
 * Send conversation.started to backend after receiving conversation.start.
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_conversation_started(const char *stream_id);

/**
 * Send conversation.stopped to backend.
 * Returns ESP_ERR_INVALID_STATE if not online.
 */
esp_err_t ws_transport_send_conversation_stopped(const char *stream_id);

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
