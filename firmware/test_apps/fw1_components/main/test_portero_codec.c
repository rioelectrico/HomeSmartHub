#include "unity.h"

#include <string.h>

#include "portero_codec.h"

/* Large structs use static storage to avoid task-stack overflow */
static portero_command_result_t  s_cmd_result;
static portero_command_request_t s_cmd_request;

/* Generic encode output buffer */
static char s_buf[512];

/* ============================================================
 * ENCODE — device.hello
 * ============================================================ */

static void test_encode_hello_no_capabilities(void)
{
    portero_device_hello_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "abc123", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "PI-000001", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);
    msg.seq = 0U;
    msg.capabilities_count = 0U;

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_device_hello(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"type\":\"device.hello\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"version\":\"v1\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"seq\":0"));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"capabilities\":[]"));
}

static void test_encode_hello_with_capability(void)
{
    portero_device_hello_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "boot-xyz", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "PI-000042", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);
    msg.seq = 0U;
    msg.capabilities_count = 1U;
    strncpy(msg.capabilities[0], "audio_pcm16_v1", PORTERO_CODEC_CAPABILITY_MAX_LEN);

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_device_hello(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"audio_pcm16_v1\""));
}

static void test_encode_hello_null_msg_rejected(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_device_hello(NULL, s_buf, sizeof(s_buf)));
}

static void test_encode_hello_null_out_rejected(void)
{
    portero_device_hello_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "boot-xyz", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "PI-000001", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_device_hello(&msg, NULL, sizeof(s_buf)));
}

static void test_encode_hello_nonzero_seq_rejected(void)
{
    portero_device_hello_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "boot-xyz", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "PI-000001", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);
    msg.seq = 1U;

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_device_hello(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_hello_invalid_device_id_rejected(void)
{
    portero_device_hello_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "boot-xyz", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "XX-000001", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_device_hello(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_hello_buffer_too_small_rejected(void)
{
    portero_device_hello_t msg;
    char tiny[10];
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,   "boot-xyz", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.device_id, "PI-000001", PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
        portero_codec_encode_device_hello(&msg, tiny, sizeof(tiny)));
}

/* ============================================================
 * ENCODE — auth.response
 * ============================================================ */

static void test_encode_auth_response_valid(void)
{
    portero_auth_response_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-abc", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq = 1U;
    /* 32 char nonce, all alphanumeric */
    strncpy(msg.nonce,  "abcdefghijklmnopqrstuvwxyz012345", PORTERO_CODEC_NONCE_MAX_LEN);
    /* 64 lowercase hex digest */
    strncpy(msg.digest,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            PORTERO_CODEC_DIGEST_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_auth_response(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"type\":\"auth.response\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"seq\":1"));
}

static void test_encode_auth_response_uppercase_digest_rejected(void)
{
    portero_auth_response_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-abc", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq = 1U;
    strncpy(msg.nonce,  "abcdefghijklmnopqrstuvwxyz012345", PORTERO_CODEC_NONCE_MAX_LEN);
    /* Uppercase digest — must be rejected */
    strncpy(msg.digest,
            "0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF",
            PORTERO_CODEC_DIGEST_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_auth_response(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_auth_response_short_digest_rejected(void)
{
    portero_auth_response_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-abc", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq = 1U;
    strncpy(msg.nonce,  "abcdefghijklmnopqrstuvwxyz012345", PORTERO_CODEC_NONCE_MAX_LEN);
    strncpy(msg.digest, "abc123", PORTERO_CODEC_DIGEST_LEN); /* too short */

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_auth_response(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_auth_response_short_nonce_rejected(void)
{
    portero_auth_response_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-abc", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq = 1U;
    strncpy(msg.nonce, "tooshort", PORTERO_CODEC_NONCE_MAX_LEN); /* < 32 */
    strncpy(msg.digest,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            PORTERO_CODEC_DIGEST_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_auth_response(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_auth_response_nonce_invalid_chars_rejected(void)
{
    portero_auth_response_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-abc", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq = 1U;
    /* space is not allowed */
    strncpy(msg.nonce, "abcdefghijklmnopqrstuvwxyz01 345", PORTERO_CODEC_NONCE_MAX_LEN);
    strncpy(msg.digest,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            PORTERO_CODEC_DIGEST_BUFFER_SIZE);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_auth_response(&msg, s_buf, sizeof(s_buf)));
}

/* ============================================================
 * ENCODE — device.status
 * ============================================================ */

static void test_encode_device_status_valid(void)
{
    portero_device_status_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,          "boot-1",       PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.firmware_version, "1.0.0",         PORTERO_CODEC_FIRMWARE_VER_MAX_LEN);
    strncpy(msg.hardware_model,   "ESP32-P4-ETH",  PORTERO_CODEC_HW_MODEL_MAX_LEN);
    msg.seq             = 2U;
    msg.uptime_seconds  = 42U;
    msg.ethernet        = PORTERO_ETHERNET_ONLINE;
    msg.camera          = PORTERO_PERIPHERAL_READY;
    msg.microphone      = PORTERO_PERIPHERAL_UNAVAILABLE;
    msg.speaker         = PORTERO_PERIPHERAL_ERROR;
    msg.free_heap_bytes = 131072U;

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_device_status(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"type\":\"device.status\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"ethernet\":\"online\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"camera\":\"ready\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"microphone\":\"unavailable\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"speaker\":\"error\""));
}

/* ============================================================
 * ENCODE — device.heartbeat
 * ============================================================ */

static void test_encode_device_heartbeat_valid(void)
{
    portero_device_heartbeat_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id, "boot-hb", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    msg.seq            = 3U;
    msg.uptime_seconds = 120U;

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_device_heartbeat(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"type\":\"device.heartbeat\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"seq\":3"));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"uptime_seconds\":120"));
}

/* ============================================================
 * ENCODE — command.ack
 * ============================================================ */

static void test_encode_command_ack_accepted(void)
{
    portero_command_ack_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,    "boot-ack",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    msg.seq        = 4U;
    msg.status     = PORTERO_ACK_ACCEPTED;
    msg.error_code = PORTERO_ERROR_CODE_NONE;

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_command_ack(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"status\":\"accepted\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"error_code\":null"));
}

static void test_encode_command_ack_rejected(void)
{
    portero_command_ack_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,    "boot-ack",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    msg.seq        = 4U;
    msg.status     = PORTERO_ACK_REJECTED;
    msg.error_code = PORTERO_ERROR_CODE_CAMERA_NOT_READY;

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_encode_command_ack(&msg, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"status\":\"rejected\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"error_code\":\"CAMERA_NOT_READY\""));
}

static void test_encode_command_ack_rejected_missing_error_code_rejected(void)
{
    portero_command_ack_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,    "boot-ack",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    msg.seq        = 4U;
    msg.status     = PORTERO_ACK_REJECTED;
    msg.error_code = PORTERO_ERROR_CODE_NONE; /* missing — must be rejected */

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_ack(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_command_ack_accepted_with_error_code_rejected(void)
{
    portero_command_ack_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,    "boot-ack",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    msg.seq        = 4U;
    msg.status     = PORTERO_ACK_ACCEPTED;
    msg.error_code = PORTERO_ERROR_CODE_INTERNAL_ERROR; /* must be rejected */

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_ack(&msg, s_buf, sizeof(s_buf)));
}

static void test_encode_command_ack_invalid_uuid_rejected(void)
{
    portero_command_ack_t msg;
    memset(&msg, 0, sizeof(msg));
    strncpy(msg.boot_id,    "boot-ack", PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(msg.command_id, "not-a-uuid", PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    msg.seq    = 4U;
    msg.status = PORTERO_ACK_ACCEPTED;

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_ack(&msg, s_buf, sizeof(s_buf)));
}

/* ============================================================
 * ENCODE — command.result
 * ============================================================ */

static void test_encode_command_result_completed(void)
{
    memset(&s_cmd_result, 0, sizeof(s_cmd_result));
    strncpy(s_cmd_result.boot_id,    "boot-res",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(s_cmd_result.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    strncpy(s_cmd_result.result_json, "{}",                                     PORTERO_CODEC_RESULT_JSON_MAX_LEN);
    s_cmd_result.seq        = 5U;
    s_cmd_result.status     = PORTERO_RESULT_COMPLETED;
    s_cmd_result.error_code = PORTERO_ERROR_CODE_NONE;

    TEST_ASSERT_EQUAL(ESP_OK,
        portero_codec_encode_command_result(&s_cmd_result, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"type\":\"command.result\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"status\":\"completed\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"error_code\":null"));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"result\":{}"));
}

static void test_encode_command_result_failed(void)
{
    memset(&s_cmd_result, 0, sizeof(s_cmd_result));
    strncpy(s_cmd_result.boot_id,    "boot-res",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(s_cmd_result.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    strncpy(s_cmd_result.result_json, "{}",                                     PORTERO_CODEC_RESULT_JSON_MAX_LEN);
    s_cmd_result.seq        = 5U;
    s_cmd_result.status     = PORTERO_RESULT_FAILED;
    s_cmd_result.error_code = PORTERO_ERROR_CODE_CAMERA_CAPTURE_FAILED;

    TEST_ASSERT_EQUAL(ESP_OK,
        portero_codec_encode_command_result(&s_cmd_result, s_buf, sizeof(s_buf)));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"status\":\"failed\""));
    TEST_ASSERT_NOT_NULL(strstr(s_buf, "\"error_code\":\"CAMERA_CAPTURE_FAILED\""));
}

static void test_encode_command_result_failed_missing_error_code_rejected(void)
{
    memset(&s_cmd_result, 0, sizeof(s_cmd_result));
    strncpy(s_cmd_result.boot_id,    "boot-res",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(s_cmd_result.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    strncpy(s_cmd_result.result_json, "{}",                                     PORTERO_CODEC_RESULT_JSON_MAX_LEN);
    s_cmd_result.seq        = 5U;
    s_cmd_result.status     = PORTERO_RESULT_FAILED;
    s_cmd_result.error_code = PORTERO_ERROR_CODE_NONE; /* missing — must be rejected */

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_result(&s_cmd_result, s_buf, sizeof(s_buf)));
}

static void test_encode_command_result_completed_with_error_code_rejected(void)
{
    memset(&s_cmd_result, 0, sizeof(s_cmd_result));
    strncpy(s_cmd_result.boot_id,    "boot-res",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(s_cmd_result.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    strncpy(s_cmd_result.result_json, "{}",                                     PORTERO_CODEC_RESULT_JSON_MAX_LEN);
    s_cmd_result.seq        = 5U;
    s_cmd_result.status     = PORTERO_RESULT_COMPLETED;
    s_cmd_result.error_code = PORTERO_ERROR_CODE_INTERNAL_ERROR; /* must be rejected */

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_result(&s_cmd_result, s_buf, sizeof(s_buf)));
}

static void test_encode_command_result_invalid_result_json_rejected(void)
{
    memset(&s_cmd_result, 0, sizeof(s_cmd_result));
    strncpy(s_cmd_result.boot_id,    "boot-res",                               PORTERO_CODEC_BOOT_ID_MAX_LEN);
    strncpy(s_cmd_result.command_id, "123e4567-e89b-12d3-a456-426614174000",   PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE);
    strncpy(s_cmd_result.result_json, "[1,2,3]",                                PORTERO_CODEC_RESULT_JSON_MAX_LEN); /* array, not object */
    s_cmd_result.seq        = 5U;
    s_cmd_result.status     = PORTERO_RESULT_COMPLETED;
    s_cmd_result.error_code = PORTERO_ERROR_CODE_NONE;

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        portero_codec_encode_command_result(&s_cmd_result, s_buf, sizeof(s_buf)));
}

/* ============================================================
 * DECODE — auth.challenge
 * ============================================================ */

static void test_decode_auth_challenge_valid(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"example_nonce_0123456789ABCDEFGHijklmnop\","
                       "\"expires_at\":\"2026-09-05T15:00:15+00:00\"}";
    portero_auth_challenge_t out;
    memset(&out, 0, sizeof(out));

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_decode_auth_challenge(json, &out));
    TEST_ASSERT_EQUAL_STRING("example_nonce_0123456789ABCDEFGHijklmnop", out.nonce);
    TEST_ASSERT_EQUAL_STRING("2026-09-05T15:00:15+00:00", out.expires_at);
}

static void test_decode_auth_challenge_null_inputs_rejected(void)
{
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, portero_codec_decode_auth_challenge(NULL, &out));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, portero_codec_decode_auth_challenge("{}", NULL));
}

static void test_decode_auth_challenge_malformed_json_rejected(void)
{
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge("{not valid json", &out));
}

static void test_decode_auth_challenge_extra_field_rejected(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"abcdefghijklmnopqrstuvwxyz012345\","
                       "\"expires_at\":\"2026-09-05T15:00:15Z\","
                       "\"extra\":\"field\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
    /* Output must be zeroed on failure */
    TEST_ASSERT_EQUAL_UINT8(0, out.nonce[0]);
}

static void test_decode_auth_challenge_missing_field_rejected(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"abcdefghijklmnopqrstuvwxyz012345\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
}

static void test_decode_auth_challenge_wrong_algorithm_rejected(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"SHA256\","
                       "\"nonce\":\"abcdefghijklmnopqrstuvwxyz012345\","
                       "\"expires_at\":\"2026-09-05T15:00:15Z\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
}

static void test_decode_auth_challenge_nonce_too_short_rejected(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"short\","
                       "\"expires_at\":\"2026-09-05T15:00:15Z\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
}

static void test_decode_auth_challenge_nonce_invalid_chars_rejected(void)
{
    const char *json = "{\"type\":\"auth.challenge\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"abcdefghijklmnopqrstuvwxyz01 345\","
                       "\"expires_at\":\"2026-09-05T15:00:15Z\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
}

static void test_decode_auth_challenge_wrong_type_rejected(void)
{
    const char *json = "{\"type\":\"auth.ok\","
                       "\"algorithm\":\"HMAC-SHA256\","
                       "\"nonce\":\"abcdefghijklmnopqrstuvwxyz012345\","
                       "\"expires_at\":\"2026-09-05T15:00:15Z\"}";
    portero_auth_challenge_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_challenge(json, &out));
}

/* ============================================================
 * DECODE — auth.ok
 * ============================================================ */

static void test_decode_auth_ok_valid(void)
{
    const char *json = "{\"type\":\"auth.ok\","
                       "\"upload_token\":\"tok-abc-123\","
                       "\"upload_expires_at\":\"2026-09-05T15:05:00+00:00\","
                       "\"heartbeat_interval_seconds\":15}";
    portero_auth_ok_t out;
    memset(&out, 0, sizeof(out));

    TEST_ASSERT_EQUAL(ESP_OK, portero_codec_decode_auth_ok(json, &out));
    TEST_ASSERT_EQUAL_STRING("tok-abc-123", out.upload_token);
    TEST_ASSERT_EQUAL_UINT32(15U, out.heartbeat_interval_seconds);
}

static void test_decode_auth_ok_string_heartbeat_rejected(void)
{
    /* Strict no-coercion: "30" string must be rejected */
    const char *json = "{\"type\":\"auth.ok\","
                       "\"upload_token\":\"tok\","
                       "\"upload_expires_at\":\"2026-09-05T15:05:00Z\","
                       "\"heartbeat_interval_seconds\":\"30\"}";
    portero_auth_ok_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_ok(json, &out));
}

static void test_decode_auth_ok_fractional_heartbeat_rejected(void)
{
    const char *json = "{\"type\":\"auth.ok\","
                       "\"upload_token\":\"tok\","
                       "\"upload_expires_at\":\"2026-09-05T15:05:00Z\","
                       "\"heartbeat_interval_seconds\":15.5}";
    portero_auth_ok_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_ok(json, &out));
}

static void test_decode_auth_ok_zero_heartbeat_rejected(void)
{
    const char *json = "{\"type\":\"auth.ok\","
                       "\"upload_token\":\"tok\","
                       "\"upload_expires_at\":\"2026-09-05T15:05:00Z\","
                       "\"heartbeat_interval_seconds\":0}";
    portero_auth_ok_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_ok(json, &out));
}

static void test_decode_auth_ok_extra_field_rejected(void)
{
    const char *json = "{\"type\":\"auth.ok\","
                       "\"upload_token\":\"tok\","
                       "\"upload_expires_at\":\"2026-09-05T15:05:00Z\","
                       "\"heartbeat_interval_seconds\":15,"
                       "\"extra\":true}";
    portero_auth_ok_t out;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_auth_ok(json, &out));
    TEST_ASSERT_EQUAL_UINT32(0U, out.heartbeat_interval_seconds);
}

/* ============================================================
 * DECODE — command.request
 * ============================================================ */

static void test_decode_command_request_camera_capture(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                       "\"command\":\"camera.capture\","
                       "\"payload\":{}}";

    TEST_ASSERT_EQUAL(ESP_OK,
        portero_codec_decode_command_request(json, &s_cmd_request));
    TEST_ASSERT_EQUAL(PORTERO_COMMAND_CAMERA_CAPTURE, s_cmd_request.command);
    TEST_ASSERT_EQUAL_STRING("123e4567-e89b-12d3-a456-426614174000",
                              s_cmd_request.command_id);
}

static void test_decode_command_request_device_status_request(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"223e4567-e89b-12d3-a456-426614174001\","
                       "\"command\":\"device.status.request\","
                       "\"payload\":{}}";

    TEST_ASSERT_EQUAL(ESP_OK,
        portero_codec_decode_command_request(json, &s_cmd_request));
    TEST_ASSERT_EQUAL(PORTERO_COMMAND_DEVICE_STATUS_REQUEST, s_cmd_request.command);
}

static void test_decode_command_request_unknown_command_rejected(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                       "\"command\":\"access.unlock\","
                       "\"payload\":{}}";

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_command_request(json, &s_cmd_request));
    TEST_ASSERT_EQUAL_UINT8(0, s_cmd_request.command_id[0]);
}

static void test_decode_command_request_invalid_uuid_rejected(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"not-a-uuid\","
                       "\"command\":\"camera.capture\","
                       "\"payload\":{}}";

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_command_request(json, &s_cmd_request));
}

static void test_decode_command_request_array_payload_rejected(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                       "\"command\":\"camera.capture\","
                       "\"payload\":[1,2,3]}";

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_command_request(json, &s_cmd_request));
}

static void test_decode_command_request_extra_field_rejected(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                       "\"command\":\"camera.capture\","
                       "\"payload\":{},"
                       "\"extra\":1}";

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_command_request(json, &s_cmd_request));
}

static void test_decode_command_request_missing_payload_rejected(void)
{
    const char *json = "{\"type\":\"command.request\","
                       "\"command_id\":\"123e4567-e89b-12d3-a456-426614174000\","
                       "\"command\":\"camera.capture\"}";

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        portero_codec_decode_command_request(json, &s_cmd_request));
}

/* ============================================================
 * Test runner
 * ============================================================ */

void run_portero_codec_tests(void)
{
    /* encode device.hello */
    RUN_TEST(test_encode_hello_no_capabilities);
    RUN_TEST(test_encode_hello_with_capability);
    RUN_TEST(test_encode_hello_null_msg_rejected);
    RUN_TEST(test_encode_hello_null_out_rejected);
    RUN_TEST(test_encode_hello_nonzero_seq_rejected);
    RUN_TEST(test_encode_hello_invalid_device_id_rejected);
    RUN_TEST(test_encode_hello_buffer_too_small_rejected);

    /* encode auth.response */
    RUN_TEST(test_encode_auth_response_valid);
    RUN_TEST(test_encode_auth_response_uppercase_digest_rejected);
    RUN_TEST(test_encode_auth_response_short_digest_rejected);
    RUN_TEST(test_encode_auth_response_short_nonce_rejected);
    RUN_TEST(test_encode_auth_response_nonce_invalid_chars_rejected);

    /* encode device.status */
    RUN_TEST(test_encode_device_status_valid);

    /* encode device.heartbeat */
    RUN_TEST(test_encode_device_heartbeat_valid);

    /* encode command.ack */
    RUN_TEST(test_encode_command_ack_accepted);
    RUN_TEST(test_encode_command_ack_rejected);
    RUN_TEST(test_encode_command_ack_rejected_missing_error_code_rejected);
    RUN_TEST(test_encode_command_ack_accepted_with_error_code_rejected);
    RUN_TEST(test_encode_command_ack_invalid_uuid_rejected);

    /* encode command.result */
    RUN_TEST(test_encode_command_result_completed);
    RUN_TEST(test_encode_command_result_failed);
    RUN_TEST(test_encode_command_result_failed_missing_error_code_rejected);
    RUN_TEST(test_encode_command_result_completed_with_error_code_rejected);
    RUN_TEST(test_encode_command_result_invalid_result_json_rejected);

    /* decode auth.challenge */
    RUN_TEST(test_decode_auth_challenge_valid);
    RUN_TEST(test_decode_auth_challenge_null_inputs_rejected);
    RUN_TEST(test_decode_auth_challenge_malformed_json_rejected);
    RUN_TEST(test_decode_auth_challenge_extra_field_rejected);
    RUN_TEST(test_decode_auth_challenge_missing_field_rejected);
    RUN_TEST(test_decode_auth_challenge_wrong_algorithm_rejected);
    RUN_TEST(test_decode_auth_challenge_nonce_too_short_rejected);
    RUN_TEST(test_decode_auth_challenge_nonce_invalid_chars_rejected);
    RUN_TEST(test_decode_auth_challenge_wrong_type_rejected);

    /* decode auth.ok */
    RUN_TEST(test_decode_auth_ok_valid);
    RUN_TEST(test_decode_auth_ok_string_heartbeat_rejected);
    RUN_TEST(test_decode_auth_ok_fractional_heartbeat_rejected);
    RUN_TEST(test_decode_auth_ok_zero_heartbeat_rejected);
    RUN_TEST(test_decode_auth_ok_extra_field_rejected);

    /* decode command.request */
    RUN_TEST(test_decode_command_request_camera_capture);
    RUN_TEST(test_decode_command_request_device_status_request);
    RUN_TEST(test_decode_command_request_unknown_command_rejected);
    RUN_TEST(test_decode_command_request_invalid_uuid_rejected);
    RUN_TEST(test_decode_command_request_array_payload_rejected);
    RUN_TEST(test_decode_command_request_extra_field_rejected);
    RUN_TEST(test_decode_command_request_missing_payload_rejected);
}
