#include "portero_codec.h"

#include <inttypes.h>
#include <string.h>

#include "cJSON.h"
#include "esp_err.h"

/* ---- Internal validation helpers ---- */

static bool is_alnum_dash_underscore(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '-' || c == '_';
}

static bool is_hex_char(char c)
{
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static bool is_hex_lower_char(char c)
{
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
}

static bool validate_boot_id(const char *s)
{
    size_t len, i;
    if (!s) return false;
    len = strnlen(s, PORTERO_CODEC_BOOT_ID_BUFFER_SIZE);
    if (len < 1U || len > PORTERO_CODEC_BOOT_ID_MAX_LEN) return false;
    for (i = 0U; i < len; i++) {
        if (!is_alnum_dash_underscore(s[i])) return false;
    }
    return true;
}

static bool validate_device_id(const char *s)
{
    size_t i;
    if (!s) return false;
    if (strnlen(s, PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE) != PORTERO_CODEC_DEVICE_ID_LEN) return false;
    if (s[0] != 'P' || s[1] != 'I' || s[2] != '-') return false;
    for (i = 3U; i < PORTERO_CODEC_DEVICE_ID_LEN; i++) {
        if (s[i] < '0' || s[i] > '9') return false;
    }
    return true;
}

static bool validate_nonce(const char *s)
{
    size_t len, i;
    if (!s) return false;
    len = strnlen(s, PORTERO_CODEC_NONCE_BUFFER_SIZE);
    if (len < PORTERO_CODEC_NONCE_MIN_LEN || len > PORTERO_CODEC_NONCE_MAX_LEN) return false;
    for (i = 0U; i < len; i++) {
        if (!is_alnum_dash_underscore(s[i])) return false;
    }
    return true;
}

static bool validate_digest(const char *s)
{
    size_t i;
    if (!s) return false;
    if (strnlen(s, PORTERO_CODEC_DIGEST_BUFFER_SIZE) != PORTERO_CODEC_DIGEST_LEN) return false;
    for (i = 0U; i < PORTERO_CODEC_DIGEST_LEN; i++) {
        if (!is_hex_lower_char(s[i])) return false;
    }
    return true;
}

static bool validate_uuid(const char *s)
{
    /* xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx */
    static const uint8_t group_lens[5] = {8U, 4U, 4U, 4U, 12U};
    size_t pos = 0U;
    uint8_t g, j;

    if (!s || strnlen(s, PORTERO_CODEC_COMMAND_ID_BUFFER_SIZE) != PORTERO_CODEC_COMMAND_ID_LEN) {
        return false;
    }
    for (g = 0U; g < 5U; g++) {
        for (j = 0U; j < group_lens[g]; j++) {
            if (!is_hex_char(s[pos++])) return false;
        }
        if (g < 4U) {
            if (s[pos++] != '-') return false;
        }
    }
    return pos == PORTERO_CODEC_COMMAND_ID_LEN;
}

static bool validate_iso8601(const char *s)
{
    const char *t_pos;
    const char *p;
    size_t len;

    if (!s) return false;
    len = strnlen(s, PORTERO_CODEC_TIMESTAMP_BUFFER_SIZE);
    /* Minimum: "2026-09-05T15:00:15Z" = 20 chars */
    if (len < 20U || len > PORTERO_CODEC_TIMESTAMP_MAX_LEN) return false;
    t_pos = strchr(s, 'T');
    if (!t_pos) return false;
    /* After T, scan for timezone marker */
    p = t_pos + 1;
    while (*p && *p != 'Z' && *p != '+' && *p != '-') {
        p++;
    }
    return *p != '\0';
}

/* ---- Enum string helpers ---- */

static const char *ethernet_to_str(portero_ethernet_status_t v)
{
    switch (v) {
        case PORTERO_ETHERNET_ONLINE:  return "online";
        case PORTERO_ETHERNET_OFFLINE: return "offline";
        case PORTERO_ETHERNET_ERROR:   return "error";
        default:                       return NULL;
    }
}

static const char *peripheral_to_str(portero_peripheral_status_t v)
{
    switch (v) {
        case PORTERO_PERIPHERAL_READY:       return "ready";
        case PORTERO_PERIPHERAL_UNAVAILABLE: return "unavailable";
        case PORTERO_PERIPHERAL_ERROR:       return "error";
        default:                             return NULL;
    }
}

static const char *ack_status_to_str(portero_ack_status_t v)
{
    switch (v) {
        case PORTERO_ACK_ACCEPTED: return "accepted";
        case PORTERO_ACK_REJECTED: return "rejected";
        default:                   return NULL;
    }
}

static const char *result_status_to_str(portero_result_status_t v)
{
    switch (v) {
        case PORTERO_RESULT_COMPLETED: return "completed";
        case PORTERO_RESULT_FAILED:    return "failed";
        default:                       return NULL;
    }
}

static const char *error_code_to_str(portero_device_error_code_t v)
{
    switch (v) {
        case PORTERO_ERROR_CODE_AUTH_FAILED:                  return "AUTH_FAILED";
        case PORTERO_ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED: return "PROTOCOL_VERSION_UNSUPPORTED";
        case PORTERO_ERROR_CODE_INVALID_COMMAND:              return "INVALID_COMMAND";
        case PORTERO_ERROR_CODE_INVALID_PARAMETER:            return "INVALID_PARAMETER";
        case PORTERO_ERROR_CODE_DEVICE_BUSY:                  return "DEVICE_BUSY";
        case PORTERO_ERROR_CODE_CAMERA_NOT_READY:             return "CAMERA_NOT_READY";
        case PORTERO_ERROR_CODE_CAMERA_CAPTURE_FAILED:        return "CAMERA_CAPTURE_FAILED";
        case PORTERO_ERROR_CODE_MIC_NOT_READY:                return "MIC_NOT_READY";
        case PORTERO_ERROR_CODE_SPEAKER_NOT_READY:            return "SPEAKER_NOT_READY";
        case PORTERO_ERROR_CODE_INTERNAL_ERROR:               return "INTERNAL_ERROR";
        default:                                              return NULL;
    }
}

static portero_command_t str_to_command(const char *s)
{
    if (!s) return (portero_command_t)-1;
    if (strcmp(s, "camera.capture") == 0)        return PORTERO_COMMAND_CAMERA_CAPTURE;
    if (strcmp(s, "device.status.request") == 0) return PORTERO_COMMAND_DEVICE_STATUS_REQUEST;
    return (portero_command_t)-1;
}

/* ---- Encode helpers ---- */

static esp_err_t add_u64(cJSON *obj, const char *key, uint64_t value)
{
    char buf[21]; /* max uint64_t = 20 digits + NUL */
    cJSON *item;

    snprintf(buf, sizeof(buf), "%" PRIu64, value);
    item = cJSON_CreateRaw(buf);
    if (!item) return ESP_ERR_NO_MEM;
    if (!cJSON_AddItemToObject(obj, key, item)) {
        cJSON_Delete(item);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static esp_err_t render_to_buf(cJSON *root, char *out, size_t out_size)
{
    char *rendered;
    size_t len;

    rendered = cJSON_PrintUnformatted(root);
    if (!rendered) return ESP_ERR_NO_MEM;

    len = strlen(rendered);
    if (len >= out_size) {
        cJSON_free(rendered);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(out, rendered, len + 1U);
    cJSON_free(rendered);
    return ESP_OK;
}

/* ---- portero_codec_encode_device_hello ---- */

esp_err_t portero_codec_encode_device_hello(const portero_device_hello_t *msg,
                                             char *out, size_t out_size)
{
    cJSON *root = NULL;
    cJSON *caps = NULL;
    esp_err_t ret;
    uint8_t i;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id))   return ESP_ERR_INVALID_ARG;
    if (!validate_device_id(msg->device_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq != 0U)                    return ESP_ERR_INVALID_ARG;
    if (msg->capabilities_count > PORTERO_CODEC_CAPABILITIES_MAX) return ESP_ERR_INVALID_ARG;
    for (i = 0U; i < msg->capabilities_count; i++) {
        size_t cap_len = strnlen(msg->capabilities[i], PORTERO_CODEC_CAPABILITY_BUFFER_SIZE);
        if (cap_len == 0U || cap_len >= PORTERO_CODEC_CAPABILITY_BUFFER_SIZE) {
            return ESP_ERR_INVALID_ARG;
        }
    }

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",      "device.hello") ||
        !cJSON_AddStringToObject(root, "version",   "v1")           ||
        !cJSON_AddStringToObject(root, "device_id", msg->device_id) ||
        !cJSON_AddStringToObject(root, "boot_id",   msg->boot_id)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq", msg->seq);
    if (ret != ESP_OK) goto cleanup;

    caps = cJSON_CreateArray();
    if (!caps) { ret = ESP_ERR_NO_MEM; goto cleanup; }
    if (!cJSON_AddItemToObject(root, "capabilities", caps)) {
        cJSON_Delete(caps);
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    for (i = 0U; i < msg->capabilities_count; i++) {
        cJSON *cap_item = cJSON_CreateString(msg->capabilities[i]);
        if (!cap_item) { ret = ESP_ERR_NO_MEM; goto cleanup; }
        if (!cJSON_AddItemToArray(caps, cap_item)) {
            cJSON_Delete(cap_item);
            ret = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    }

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_auth_response ---- */

esp_err_t portero_codec_encode_auth_response(const portero_auth_response_t *msg,
                                              char *out, size_t out_size)
{
    cJSON *root = NULL;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;
    if (!validate_nonce(msg->nonce))     return ESP_ERR_INVALID_ARG;
    if (!validate_digest(msg->digest))   return ESP_ERR_INVALID_ARG;

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",    "auth.response") ||
        !cJSON_AddStringToObject(root, "boot_id", msg->boot_id)    ||
        !cJSON_AddStringToObject(root, "nonce",   msg->nonce)      ||
        !cJSON_AddStringToObject(root, "digest",  msg->digest)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq", msg->seq);
    if (ret != ESP_OK) goto cleanup;

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_device_status ---- */

esp_err_t portero_codec_encode_device_status(const portero_device_status_t *msg,
                                              char *out, size_t out_size)
{
    cJSON *root = NULL;
    const char *eth_str;
    const char *cam_str;
    const char *mic_str;
    const char *spk_str;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;

    if (strnlen(msg->firmware_version, PORTERO_CODEC_FIRMWARE_VER_BUFFER_SIZE) == 0U ||
        strnlen(msg->firmware_version, PORTERO_CODEC_FIRMWARE_VER_BUFFER_SIZE) > PORTERO_CODEC_FIRMWARE_VER_MAX_LEN) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strnlen(msg->hardware_model, PORTERO_CODEC_HW_MODEL_BUFFER_SIZE) == 0U ||
        strnlen(msg->hardware_model, PORTERO_CODEC_HW_MODEL_BUFFER_SIZE) > PORTERO_CODEC_HW_MODEL_MAX_LEN) {
        return ESP_ERR_INVALID_ARG;
    }

    eth_str = ethernet_to_str(msg->ethernet);
    cam_str = peripheral_to_str(msg->camera);
    mic_str = peripheral_to_str(msg->microphone);
    spk_str = peripheral_to_str(msg->speaker);
    if (!eth_str || !cam_str || !mic_str || !spk_str) return ESP_ERR_INVALID_ARG;

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",             "device.status")      ||
        !cJSON_AddStringToObject(root, "boot_id",          msg->boot_id)         ||
        !cJSON_AddStringToObject(root, "firmware_version", msg->firmware_version) ||
        !cJSON_AddStringToObject(root, "hardware_model",   msg->hardware_model)   ||
        !cJSON_AddStringToObject(root, "ethernet",         eth_str)              ||
        !cJSON_AddStringToObject(root, "camera",           cam_str)              ||
        !cJSON_AddStringToObject(root, "microphone",       mic_str)              ||
        !cJSON_AddStringToObject(root, "speaker",          spk_str)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq",            msg->seq);
    if (ret != ESP_OK) goto cleanup;
    ret = add_u64(root, "uptime_seconds", msg->uptime_seconds);
    if (ret != ESP_OK) goto cleanup;
    ret = add_u64(root, "free_heap_bytes", msg->free_heap_bytes);
    if (ret != ESP_OK) goto cleanup;

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_device_heartbeat ---- */

esp_err_t portero_codec_encode_device_heartbeat(const portero_device_heartbeat_t *msg,
                                                 char *out, size_t out_size)
{
    cJSON *root = NULL;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",    "device.heartbeat") ||
        !cJSON_AddStringToObject(root, "boot_id", msg->boot_id)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq",            msg->seq);
    if (ret != ESP_OK) goto cleanup;
    ret = add_u64(root, "uptime_seconds", msg->uptime_seconds);
    if (ret != ESP_OK) goto cleanup;

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_command_ack ---- */

esp_err_t portero_codec_encode_command_ack(const portero_command_ack_t *msg,
                                            char *out, size_t out_size)
{
    cJSON *root = NULL;
    const char *status_str;
    const char *ec_str;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;
    if (!validate_uuid(msg->command_id)) return ESP_ERR_INVALID_ARG;

    status_str = ack_status_to_str(msg->status);
    if (!status_str) return ESP_ERR_INVALID_ARG;

    if (msg->status == PORTERO_ACK_REJECTED) {
        ec_str = error_code_to_str(msg->error_code);
        if (!ec_str) return ESP_ERR_INVALID_ARG; /* rejected requires a valid error_code */
    } else {
        if (msg->error_code != PORTERO_ERROR_CODE_NONE) return ESP_ERR_INVALID_ARG;
        ec_str = NULL;
    }

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",       "command.ack")  ||
        !cJSON_AddStringToObject(root, "boot_id",    msg->boot_id)    ||
        !cJSON_AddStringToObject(root, "command_id", msg->command_id) ||
        !cJSON_AddStringToObject(root, "status",     status_str)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq", msg->seq);
    if (ret != ESP_OK) goto cleanup;

    if (ec_str) {
        if (!cJSON_AddStringToObject(root, "error_code", ec_str)) {
            ret = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    } else {
        if (!cJSON_AddNullToObject(root, "error_code")) {
            ret = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    }

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_command_result ---- */

esp_err_t portero_codec_encode_command_result(const portero_command_result_t *msg,
                                               char *out, size_t out_size)
{
    cJSON *root        = NULL;
    cJSON *result_node = NULL;
    const char *status_str;
    const char *ec_str;
    size_t result_len;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;
    if (!validate_uuid(msg->command_id)) return ESP_ERR_INVALID_ARG;

    status_str = result_status_to_str(msg->status);
    if (!status_str) return ESP_ERR_INVALID_ARG;

    if (msg->status == PORTERO_RESULT_FAILED) {
        ec_str = error_code_to_str(msg->error_code);
        if (!ec_str) return ESP_ERR_INVALID_ARG; /* failed requires a valid error_code */
    } else {
        if (msg->error_code != PORTERO_ERROR_CODE_NONE) return ESP_ERR_INVALID_ARG;
        ec_str = NULL;
    }

    result_len = strnlen(msg->result_json, PORTERO_CODEC_RESULT_JSON_BUFFER_SIZE);
    if (result_len == 0U || result_len > PORTERO_CODEC_RESULT_JSON_MAX_LEN) {
        return ESP_ERR_INVALID_ARG;
    }

    /* Validate and parse result_json — must be a JSON object */
    result_node = cJSON_Parse(msg->result_json);
    if (!result_node) return ESP_ERR_INVALID_ARG;
    if (!cJSON_IsObject(result_node)) {
        cJSON_Delete(result_node);
        return ESP_ERR_INVALID_ARG;
    }

    root = cJSON_CreateObject();
    if (!root) { cJSON_Delete(result_node); return ESP_ERR_NO_MEM; }

    if (!cJSON_AddStringToObject(root, "type",       "command.result") ||
        !cJSON_AddStringToObject(root, "boot_id",    msg->boot_id)     ||
        !cJSON_AddStringToObject(root, "command_id", msg->command_id)  ||
        !cJSON_AddStringToObject(root, "status",     status_str)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    ret = add_u64(root, "seq", msg->seq);
    if (ret != ESP_OK) goto cleanup;

    /* result_node ownership transfers to root on success */
    if (!cJSON_AddItemToObject(root, "result", result_node)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    result_node = NULL; /* root owns it now */

    if (ec_str) {
        if (!cJSON_AddStringToObject(root, "error_code", ec_str)) {
            ret = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    } else {
        if (!cJSON_AddNullToObject(root, "error_code")) {
            ret = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    }

    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(result_node);
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_decode_auth_challenge ---- */

esp_err_t portero_codec_decode_auth_challenge(const char *json,
                                               portero_auth_challenge_t *out)
{
    cJSON *root       = NULL;
    cJSON *type_item;
    cJSON *algo_item;
    cJSON *nonce_item;
    cJSON *exp_item;
    esp_err_t ret     = ESP_ERR_INVALID_RESPONSE;

    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    root = cJSON_Parse(json);
    if (!root) return ESP_ERR_INVALID_RESPONSE;
    if (!cJSON_IsObject(root)) goto cleanup;

    /* Reject extra fields: expect exactly 4 */
    if (cJSON_GetArraySize(root) != 4) goto cleanup;

    type_item  = cJSON_GetObjectItemCaseSensitive(root, "type");
    algo_item  = cJSON_GetObjectItemCaseSensitive(root, "algorithm");
    nonce_item = cJSON_GetObjectItemCaseSensitive(root, "nonce");
    exp_item   = cJSON_GetObjectItemCaseSensitive(root, "expires_at");

    if (!cJSON_IsString(type_item) || strcmp(type_item->valuestring, "auth.challenge") != 0) goto cleanup;
    if (!cJSON_IsString(algo_item) || strcmp(algo_item->valuestring, "HMAC-SHA256") != 0)    goto cleanup;
    if (!cJSON_IsString(nonce_item) || !validate_nonce(nonce_item->valuestring))             goto cleanup;
    if (!cJSON_IsString(exp_item)  || !validate_iso8601(exp_item->valuestring))              goto cleanup;

    strncpy(out->nonce,      nonce_item->valuestring, PORTERO_CODEC_NONCE_MAX_LEN);
    out->nonce[PORTERO_CODEC_NONCE_MAX_LEN] = '\0';
    strncpy(out->expires_at, exp_item->valuestring,   PORTERO_CODEC_TIMESTAMP_MAX_LEN);
    out->expires_at[PORTERO_CODEC_TIMESTAMP_MAX_LEN] = '\0';

    ret = ESP_OK;

cleanup:
    cJSON_Delete(root);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}

/* ---- portero_codec_decode_auth_ok ---- */

esp_err_t portero_codec_decode_auth_ok(const char *json,
                                        portero_auth_ok_t *out)
{
    cJSON *root     = NULL;
    cJSON *type_item;
    cJSON *tok_item;
    cJSON *exp_item;
    cJSON *hb_item;
    double hb_val;
    uint32_t hb_u32;
    esp_err_t ret   = ESP_ERR_INVALID_RESPONSE;

    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    root = cJSON_Parse(json);
    if (!root) return ESP_ERR_INVALID_RESPONSE;
    if (!cJSON_IsObject(root)) goto cleanup;

    /* Reject extra fields: expect exactly 4 */
    if (cJSON_GetArraySize(root) != 4) goto cleanup;

    type_item = cJSON_GetObjectItemCaseSensitive(root, "type");
    tok_item  = cJSON_GetObjectItemCaseSensitive(root, "upload_token");
    exp_item  = cJSON_GetObjectItemCaseSensitive(root, "upload_expires_at");
    hb_item   = cJSON_GetObjectItemCaseSensitive(root, "heartbeat_interval_seconds");

    if (!cJSON_IsString(type_item) || strcmp(type_item->valuestring, "auth.ok") != 0) goto cleanup;

    if (!cJSON_IsString(tok_item)) goto cleanup;
    if (strnlen(tok_item->valuestring, PORTERO_CODEC_UPLOAD_TOKEN_BUFFER_SIZE) == 0U ||
        strnlen(tok_item->valuestring, PORTERO_CODEC_UPLOAD_TOKEN_BUFFER_SIZE) > PORTERO_CODEC_UPLOAD_TOKEN_MAX_LEN) {
        goto cleanup;
    }

    if (!cJSON_IsString(exp_item) || !validate_iso8601(exp_item->valuestring)) goto cleanup;

    if (!cJSON_IsNumber(hb_item)) goto cleanup;
    hb_val = hb_item->valuedouble;
    if (hb_val < 1.0 || hb_val > (double)UINT32_MAX) goto cleanup;
    hb_u32 = (uint32_t)hb_val;
    if ((double)hb_u32 != hb_val) goto cleanup; /* reject fractional */

    strncpy(out->upload_token,      tok_item->valuestring, PORTERO_CODEC_UPLOAD_TOKEN_MAX_LEN);
    out->upload_token[PORTERO_CODEC_UPLOAD_TOKEN_MAX_LEN] = '\0';
    strncpy(out->upload_expires_at, exp_item->valuestring, PORTERO_CODEC_TIMESTAMP_MAX_LEN);
    out->upload_expires_at[PORTERO_CODEC_TIMESTAMP_MAX_LEN] = '\0';
    out->heartbeat_interval_seconds = hb_u32;

    ret = ESP_OK;

cleanup:
    cJSON_Delete(root);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}

/* ---- Conversation decode helper ---- */

static esp_err_t decode_conversation_with_stream(const char *json,
                                                   const char *expected_type,
                                                   char *out_stream_id)
{
    cJSON *root, *type_item, *sid_item;
    esp_err_t ret = ESP_ERR_INVALID_RESPONSE;

    root = cJSON_Parse(json);
    if (!root) return ESP_ERR_INVALID_RESPONSE;
    if (!cJSON_IsObject(root)) goto cleanup;
    if (cJSON_GetArraySize(root) != 2) goto cleanup;

    type_item = cJSON_GetObjectItemCaseSensitive(root, "type");
    sid_item  = cJSON_GetObjectItemCaseSensitive(root, "stream_id");

    if (!cJSON_IsString(type_item) || strcmp(type_item->valuestring, expected_type) != 0) goto cleanup;
    if (!cJSON_IsString(sid_item)  || !validate_uuid(sid_item->valuestring)) goto cleanup;

    strncpy(out_stream_id, sid_item->valuestring, PORTERO_CODEC_STREAM_ID_LEN);
    out_stream_id[PORTERO_CODEC_STREAM_ID_LEN] = '\0';
    ret = ESP_OK;

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_decode_conversation_start ---- */

esp_err_t portero_codec_decode_conversation_start(const char *json,
                                                   portero_conversation_start_t *out)
{
    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));
    esp_err_t ret = decode_conversation_with_stream(json, "conversation.start", out->stream_id);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}

/* ---- portero_codec_decode_conversation_stop ---- */

esp_err_t portero_codec_decode_conversation_stop(const char *json,
                                                  portero_conversation_stop_t *out)
{
    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));
    esp_err_t ret = decode_conversation_with_stream(json, "conversation.stop", out->stream_id);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}

/* ---- portero_codec_decode_conversation_audio_clear ---- */

esp_err_t portero_codec_decode_conversation_audio_clear(const char *json,
                                                         portero_conversation_audio_clear_t *out)
{
    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));
    esp_err_t ret = decode_conversation_with_stream(json, "conversation.audio.clear", out->stream_id);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}

/* ---- Conversation encode helper ---- */

static esp_err_t encode_conversation_event(const char *type,
                                            const char *boot_id,
                                            const char *stream_id,
                                            uint64_t seq,
                                            char *out, size_t out_size)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    esp_err_t ret;
    if (!cJSON_AddStringToObject(root, "type",      type)      ||
        !cJSON_AddStringToObject(root, "boot_id",   boot_id)   ||
        !cJSON_AddStringToObject(root, "stream_id", stream_id)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    ret = add_u64(root, "seq", seq);
    if (ret != ESP_OK) goto cleanup;
    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_device_ring ---- */

esp_err_t portero_codec_encode_device_ring(const portero_device_ring_t *msg,
                                            char *out, size_t out_size)
{
    cJSON *root = NULL;
    esp_err_t ret;

    if (!msg || !out || out_size == 0U) return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id)) return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;

    root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;

    if (!cJSON_AddStringToObject(root, "type",    "device.ring") ||
        !cJSON_AddStringToObject(root, "boot_id", msg->boot_id)) {
        ret = ESP_ERR_NO_MEM;
        goto cleanup;
    }
    ret = add_u64(root, "seq", msg->seq);
    if (ret != ESP_OK) goto cleanup;
    ret = render_to_buf(root, out, out_size);

cleanup:
    cJSON_Delete(root);
    return ret;
}

/* ---- portero_codec_encode_conversation_started ---- */

esp_err_t portero_codec_encode_conversation_started(const portero_conversation_started_t *msg,
                                                     char *out, size_t out_size)
{
    if (!msg || !out || out_size == 0U)   return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id))  return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;
    if (!validate_uuid(msg->stream_id))   return ESP_ERR_INVALID_ARG;
    return encode_conversation_event("conversation.started",
                                     msg->boot_id, msg->stream_id, msg->seq,
                                     out, out_size);
}

/* ---- portero_codec_encode_conversation_stopped ---- */

esp_err_t portero_codec_encode_conversation_stopped(const portero_conversation_stopped_t *msg,
                                                     char *out, size_t out_size)
{
    if (!msg || !out || out_size == 0U)   return ESP_ERR_INVALID_ARG;
    if (!validate_boot_id(msg->boot_id))  return ESP_ERR_INVALID_ARG;
    if (msg->seq > PORTERO_CODEC_MAX_SEQ) return ESP_ERR_INVALID_ARG;
    if (!validate_uuid(msg->stream_id))   return ESP_ERR_INVALID_ARG;
    return encode_conversation_event("conversation.stopped",
                                     msg->boot_id, msg->stream_id, msg->seq,
                                     out, out_size);
}

/* ---- portero_codec_decode_command_request ---- */

esp_err_t portero_codec_decode_command_request(const char *json,
                                                portero_command_request_t *out)
{
    cJSON *root       = NULL;
    cJSON *type_item;
    cJSON *cid_item;
    cJSON *cmd_item;
    cJSON *pay_item;
    char *payload_str = NULL;
    portero_command_t cmd;
    size_t pay_len;
    esp_err_t ret     = ESP_ERR_INVALID_RESPONSE;

    if (!json || !out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    root = cJSON_Parse(json);
    if (!root) return ESP_ERR_INVALID_RESPONSE;
    if (!cJSON_IsObject(root)) goto cleanup;

    /* Reject extra fields: expect exactly 4 */
    if (cJSON_GetArraySize(root) != 4) goto cleanup;

    type_item = cJSON_GetObjectItemCaseSensitive(root, "type");
    cid_item  = cJSON_GetObjectItemCaseSensitive(root, "command_id");
    cmd_item  = cJSON_GetObjectItemCaseSensitive(root, "command");
    pay_item  = cJSON_GetObjectItemCaseSensitive(root, "payload");

    if (!cJSON_IsString(type_item) || strcmp(type_item->valuestring, "command.request") != 0) goto cleanup;

    if (!cJSON_IsString(cid_item) || !validate_uuid(cid_item->valuestring)) goto cleanup;

    if (!cJSON_IsString(cmd_item)) goto cleanup;
    cmd = str_to_command(cmd_item->valuestring);
    if ((int)cmd < 0) goto cleanup; /* unknown command */

    if (!cJSON_IsObject(pay_item)) goto cleanup;

    /* Serialize payload back to string */
    payload_str = cJSON_PrintUnformatted(pay_item);
    if (!payload_str) { ret = ESP_ERR_NO_MEM; goto cleanup; }

    pay_len = strlen(payload_str);
    if (pay_len > PORTERO_CODEC_PAYLOAD_JSON_MAX_LEN) goto cleanup;

    strncpy(out->command_id,   cid_item->valuestring, PORTERO_CODEC_COMMAND_ID_LEN);
    out->command_id[PORTERO_CODEC_COMMAND_ID_LEN] = '\0';
    out->command = cmd;
    memcpy(out->payload_json, payload_str, pay_len + 1U);

    ret = ESP_OK;

cleanup:
    if (payload_str) cJSON_free(payload_str);
    cJSON_Delete(root);
    if (ret != ESP_OK) memset(out, 0, sizeof(*out));
    return ret;
}
