#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Size constants ---- */

#define PORTERO_CODEC_BOOT_ID_MAX_LEN           128U
#define PORTERO_CODEC_BOOT_ID_BUFFER_SIZE       (PORTERO_CODEC_BOOT_ID_MAX_LEN + 1U)

#define PORTERO_CODEC_DEVICE_ID_LEN             9U
#define PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE     (PORTERO_CODEC_DEVICE_ID_LEN + 1U)

#define PORTERO_CODEC_NONCE_MIN_LEN             32U
#define PORTERO_CODEC_NONCE_MAX_LEN             128U
#define PORTERO_CODEC_NONCE_BUFFER_SIZE         (PORTERO_CODEC_NONCE_MAX_LEN + 1U)

#define PORTERO_CODEC_DIGEST_LEN                64U
#define PORTERO_CODEC_DIGEST_BUFFER_SIZE        (PORTERO_CODEC_DIGEST_LEN + 1U)

#define PORTERO_CODEC_COMMAND_ID_LEN            36U
#define PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE    (PORTERO_CODEC_COMMAND_ID_LEN + 1U)

#define PORTERO_CODEC_FIRMWARE_VER_MAX_LEN      64U
#define PORTERO_CODEC_FIRMWARE_VER_BUFFER_SIZE  (PORTERO_CODEC_FIRMWARE_VER_MAX_LEN + 1U)

#define PORTERO_CODEC_HW_MODEL_MAX_LEN          128U
#define PORTERO_CODEC_HW_MODEL_BUFFER_SIZE      (PORTERO_CODEC_HW_MODEL_MAX_LEN + 1U)

#define PORTERO_CODEC_CAPABILITY_MAX_LEN        32U
#define PORTERO_CODEC_CAPABILITY_BUFFER_SIZE    (PORTERO_CODEC_CAPABILITY_MAX_LEN + 1U)
#define PORTERO_CODEC_CAPABILITIES_MAX          8U

#define PORTERO_CODEC_ERROR_CODE_MAX_LEN        40U
#define PORTERO_CODEC_ERROR_CODE_BUFFER_SIZE    (PORTERO_CODEC_ERROR_CODE_MAX_LEN + 1U)

#define PORTERO_CODEC_UPLOAD_TOKEN_MAX_LEN      256U
#define PORTERO_CODEC_UPLOAD_TOKEN_BUFFER_SIZE  (PORTERO_CODEC_UPLOAD_TOKEN_MAX_LEN + 1U)

#define PORTERO_CODEC_TIMESTAMP_MAX_LEN         64U
#define PORTERO_CODEC_TIMESTAMP_BUFFER_SIZE     (PORTERO_CODEC_TIMESTAMP_MAX_LEN + 1U)

#define PORTERO_CODEC_RESULT_JSON_MAX_LEN       8192U
#define PORTERO_CODEC_RESULT_JSON_BUFFER_SIZE   (PORTERO_CODEC_RESULT_JSON_MAX_LEN + 1U)

#define PORTERO_CODEC_PAYLOAD_JSON_MAX_LEN      8192U
#define PORTERO_CODEC_PAYLOAD_JSON_BUFFER_SIZE  (PORTERO_CODEC_PAYLOAD_JSON_MAX_LEN + 1U)

#define PORTERO_CODEC_STREAM_ID_LEN             36U   /* UUID format, same as COMMAND_ID_LEN */
#define PORTERO_CODEC_STREAM_ID_BUFFER_SIZE     (PORTERO_CODEC_STREAM_ID_LEN + 1U)

#define PORTERO_CODEC_CONV_ERROR_CODE_MAX_LEN      40U
#define PORTERO_CODEC_CONV_ERROR_CODE_BUFFER_SIZE  (PORTERO_CODEC_CONV_ERROR_CODE_MAX_LEN + 1U)

#define PORTERO_CODEC_CONV_OUTCOME_MAX_LEN         32U
#define PORTERO_CODEC_CONV_OUTCOME_BUFFER_SIZE     (PORTERO_CODEC_CONV_OUTCOME_MAX_LEN + 1U)

#define PORTERO_CODEC_MAX_SEQ                   9223372036854775807ULL

/* ---- Enums ---- */

typedef enum {
    PORTERO_ETHERNET_ONLINE = 0,
    PORTERO_ETHERNET_OFFLINE,
    PORTERO_ETHERNET_ERROR,
} portero_ethernet_status_t;

typedef enum {
    PORTERO_PERIPHERAL_READY = 0,
    PORTERO_PERIPHERAL_UNAVAILABLE,
    PORTERO_PERIPHERAL_ERROR,
} portero_peripheral_status_t;

typedef enum {
    PORTERO_ACK_ACCEPTED = 0,
    PORTERO_ACK_REJECTED,
} portero_ack_status_t;

typedef enum {
    PORTERO_RESULT_COMPLETED = 0,
    PORTERO_RESULT_FAILED,
} portero_result_status_t;

typedef enum {
    PORTERO_COMMAND_CAMERA_CAPTURE = 0,
    PORTERO_COMMAND_DEVICE_STATUS_REQUEST,
} portero_command_t;

typedef enum {
    PORTERO_ERROR_CODE_NONE = 0,
    PORTERO_ERROR_CODE_AUTH_FAILED,
    PORTERO_ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED,
    PORTERO_ERROR_CODE_INVALID_COMMAND,
    PORTERO_ERROR_CODE_INVALID_PARAMETER,
    PORTERO_ERROR_CODE_DEVICE_BUSY,
    PORTERO_ERROR_CODE_CAMERA_NOT_READY,
    PORTERO_ERROR_CODE_CAMERA_CAPTURE_FAILED,
    PORTERO_ERROR_CODE_MIC_NOT_READY,
    PORTERO_ERROR_CODE_SPEAKER_NOT_READY,
    PORTERO_ERROR_CODE_INTERNAL_ERROR,
} portero_device_error_code_t;

/* ---- Encode structs (ESP32 -> backend) ---- */

typedef struct {
    char     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    char     device_id[PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE];
    char     capabilities[PORTERO_CODEC_CAPABILITIES_MAX][PORTERO_CODEC_CAPABILITY_BUFFER_SIZE];
    uint8_t  capabilities_count;
    uint64_t seq; /* Must be 0 */
} portero_device_hello_t;

typedef struct {
    char     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t seq;
    char     nonce[PORTERO_CODEC_NONCE_BUFFER_SIZE];
    char     digest[PORTERO_CODEC_DIGEST_BUFFER_SIZE];
} portero_auth_response_t;

typedef struct {
    char                     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t                 seq;
    char                     firmware_version[PORTERO_CODEC_FIRMWARE_VER_BUFFER_SIZE];
    char                     hardware_model[PORTERO_CODEC_HW_MODEL_BUFFER_SIZE];
    uint64_t                 uptime_seconds;
    portero_ethernet_status_t    ethernet;
    portero_peripheral_status_t  camera;
    portero_peripheral_status_t  microphone;
    portero_peripheral_status_t  speaker;
    uint64_t                 free_heap_bytes;
} portero_device_status_t;

typedef struct {
    char     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t seq;
    uint64_t uptime_seconds;
} portero_device_heartbeat_t;

typedef struct {
    char                       boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t                   seq;
    char                       command_id[PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE];
    portero_ack_status_t       status;
    portero_device_error_code_t error_code; /* NONE when status=accepted */
} portero_command_ack_t;

typedef struct {
    char                        boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t                    seq;
    char                        command_id[PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE];
    portero_result_status_t     status;
    char                        result_json[PORTERO_CODEC_RESULT_JSON_BUFFER_SIZE]; /* JSON object, e.g. "{}" */
    portero_device_error_code_t error_code; /* NONE when status=completed */
} portero_command_result_t;

/* ---- Conversation structs: backend -> ESP32 (decode) ---- */

typedef struct {
    char stream_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
} portero_conversation_audio_clear_t;

/* conversation.started: generated by backend in response to conversation.start */
typedef struct {
    char conversation_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE]; /* UUID */
    char stream_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];       /* UUID */
} portero_conversation_started_t;

/* conversation.ended: backend terminates the conversation */
typedef struct {
    char conversation_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
    char outcome[PORTERO_CODEC_CONV_OUTCOME_BUFFER_SIZE]; /* "completed", "failed", etc. */
} portero_conversation_ended_t;

/* conversation.error: backend rejects or aborts the conversation */
typedef struct {
    char code[PORTERO_CODEC_CONV_ERROR_CODE_BUFFER_SIZE]; /* e.g. "AI_UNAVAILABLE" */
} portero_conversation_error_t;

/* ---- Conversation structs: ESP32 -> backend (encode) ---- */

/* outgoing conversation.start (device initiates, no IDs — backend generates them) */
typedef struct {
    char     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t seq;
} portero_conversation_start_req_t;

/* outgoing conversation.stop (device terminates; version:1 and reason hardcoded in encoder) */
typedef struct {
    char     boot_id[PORTERO_CODEC_BOOT_ID_BUFFER_SIZE];
    uint64_t seq;
    char     conversation_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
} portero_conversation_stop_req_t;

/* ---- Decode structs (backend -> ESP32) ---- */

typedef struct {
    char nonce[PORTERO_CODEC_NONCE_BUFFER_SIZE];
    char expires_at[PORTERO_CODEC_TIMESTAMP_BUFFER_SIZE];
} portero_auth_challenge_t;

typedef struct {
    char     upload_token[PORTERO_CODEC_UPLOAD_TOKEN_BUFFER_SIZE];
    char     upload_expires_at[PORTERO_CODEC_TIMESTAMP_BUFFER_SIZE];
    uint32_t heartbeat_interval_seconds;
} portero_auth_ok_t;

typedef struct {
    char             command_id[PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE];
    portero_command_t command;
    char             payload_json[PORTERO_CODEC_PAYLOAD_JSON_BUFFER_SIZE]; /* JSON object, e.g. "{}" */
} portero_command_request_t;

/* ---- Encode: ESP32 -> backend ---- */

esp_err_t portero_codec_encode_device_hello(const portero_device_hello_t *msg,
                                             char *out, size_t out_size);

esp_err_t portero_codec_encode_auth_response(const portero_auth_response_t *msg,
                                              char *out, size_t out_size);

esp_err_t portero_codec_encode_device_status(const portero_device_status_t *msg,
                                              char *out, size_t out_size);

esp_err_t portero_codec_encode_device_heartbeat(const portero_device_heartbeat_t *msg,
                                                 char *out, size_t out_size);

esp_err_t portero_codec_encode_command_ack(const portero_command_ack_t *msg,
                                            char *out, size_t out_size);

esp_err_t portero_codec_encode_command_result(const portero_command_result_t *msg,
                                               char *out, size_t out_size);

/* ---- Decode: backend -> ESP32 ---- */

esp_err_t portero_codec_decode_auth_challenge(const char *json,
                                               portero_auth_challenge_t *out);

esp_err_t portero_codec_decode_auth_ok(const char *json,
                                        portero_auth_ok_t *out);

esp_err_t portero_codec_decode_command_request(const char *json,
                                                portero_command_request_t *out);

esp_err_t portero_codec_decode_conversation_audio_clear(const char *json,
                                                         portero_conversation_audio_clear_t *out);

esp_err_t portero_codec_decode_conversation_started(const char *json,
                                                     portero_conversation_started_t *out);

esp_err_t portero_codec_decode_conversation_ended(const char *json,
                                                   portero_conversation_ended_t *out);

esp_err_t portero_codec_decode_conversation_error(const char *json,
                                                    portero_conversation_error_t *out);

/* ---- Encode: ESP32 -> backend (conversation) ---- */

esp_err_t portero_codec_encode_conversation_start(const portero_conversation_start_req_t *msg,
                                                   char *out, size_t out_size);

esp_err_t portero_codec_encode_conversation_stop(const portero_conversation_stop_req_t *msg,
                                                  char *out, size_t out_size);

#ifdef __cplusplus
}
#endif
