#include "ws_transport.h"

#include <inttypes.h>
#include <string.h>

#include "cJSON.h"
#include "device_auth.h"
#include "device_config.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"
#include "portero_codec.h"

static const char *TAG = "ws_transport";

#define TX_BUF_SIZE         1024
#define TX_CMD_RESULT_SIZE  10240
#define RX_BUF_SIZE         12288
#define SEND_TIMEOUT        pdMS_TO_TICKS(3000)

/* ---- State (all access from WebSocket task except ws_transport_is_online) ---- */

static esp_websocket_client_handle_t s_client    = NULL;
static ws_transport_cb_t             s_cb        = NULL;
static void                         *s_ctx       = NULL;
static esp_timer_handle_t            s_hb_timer  = NULL;
static esp_timer_handle_t            s_hs_timer  = NULL; /* handshake watchdog */
static esp_timer_handle_t            s_rc_timer  = NULL; /* reconnect backoff */
static volatile bool                 s_online    = false;
static uint64_t                      s_seq       = 0;
static uint8_t                       s_rc_attempt = 0;   /* reconnect attempt# */

static char s_device_id[PORTERO_CODEC_DEVICE_ID_BUFFER_SIZE];
static char s_secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE];
static char s_boot_id[DEVICE_AUTH_BOOT_ID_BUFFER_SIZE];

static char s_rx_buf[RX_BUF_SIZE];
static int  s_rx_len = 0;

/* ---- Helpers ---- */

static esp_err_t send_hello(void)
{
    portero_device_hello_t msg = {0};
    snprintf(msg.boot_id,   sizeof(msg.boot_id),   "%s", s_boot_id);
    snprintf(msg.device_id, sizeof(msg.device_id), "%s", s_device_id);
    msg.capabilities_count = 0;
    msg.seq = 0;

    static char buf[TX_BUF_SIZE]; /* static: called only from websocket task */
    esp_err_t err = portero_codec_encode_device_hello(&msg, buf, sizeof(buf));
    if (err != ESP_OK) return err;

    int n = esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    return (n >= 0) ? ESP_OK : ESP_FAIL;
}

static esp_err_t send_auth_response(const portero_auth_challenge_t *challenge)
{
    char hmac[DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE];
    esp_err_t err = device_auth_hmac_hex(s_secret, s_device_id, s_boot_id,
                                          challenge->nonce, hmac);
    if (err != ESP_OK) return err;

    portero_auth_response_t msg = {0};
    snprintf(msg.boot_id, sizeof(msg.boot_id), "%s", s_boot_id);
    msg.seq = ++s_seq;
    snprintf(msg.nonce,  sizeof(msg.nonce),  "%s", challenge->nonce);
    snprintf(msg.digest, sizeof(msg.digest), "%s", hmac);

    device_auth_zeroize(hmac, sizeof(hmac));

    static char buf[TX_BUF_SIZE]; /* static: called only from websocket task */
    err = portero_codec_encode_auth_response(&msg, buf, sizeof(buf));
    if (err != ESP_OK) return err;

    int n = esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    return (n >= 0) ? ESP_OK : ESP_FAIL;
}

static void send_heartbeat_now(void)
{
    portero_device_heartbeat_t msg = {0};
    snprintf(msg.boot_id, sizeof(msg.boot_id), "%s", s_boot_id);
    msg.seq            = ++s_seq;
    msg.uptime_seconds = (uint64_t)(esp_timer_get_time() / 1000000ULL);

    static char buf[TX_BUF_SIZE]; /* static: called only from websocket task */
    if (portero_codec_encode_device_heartbeat(&msg, buf, sizeof(buf)) == ESP_OK) {
        esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    }
}

static uint32_t reconnect_delay_ms(uint8_t attempt)
{
    /* 1 → 2 → 4 → 8 → 15 → 30 → 30 … seconds */
    static const uint32_t tbl[] = {1000, 2000, 4000, 8000, 15000, 30000};
    if (attempt >= 6) return 30000;
    return tbl[attempt];
}

static void reconnect_timer_cb(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "Reconnecting (attempt %d)…", s_rc_attempt);
    esp_websocket_client_start(s_client);
}

static void handshake_timer_cb(void *arg)
{
    (void)arg;
    /* auth.ok not received within 10 s — close to trigger backoff reconnect */
    ESP_LOGW(TAG, "Handshake timeout — closing to reconnect");
    esp_websocket_client_close(s_client, pdMS_TO_TICKS(3000));
}

static esp_err_t send_device_status(void)
{
    const esp_app_desc_t *desc = esp_app_get_description();
    portero_device_status_t msg = {0};
    snprintf(msg.boot_id,          sizeof(msg.boot_id),          "%s", s_boot_id);
    snprintf(msg.firmware_version, sizeof(msg.firmware_version), "%s", desc->version);
    snprintf(msg.hardware_model,   sizeof(msg.hardware_model),   "Waveshare-ESP32-P4-ETH");
    msg.seq             = ++s_seq;
    msg.uptime_seconds  = (uint64_t)(esp_timer_get_time() / 1000000ULL);
    msg.ethernet        = PORTERO_ETHERNET_ONLINE;
    msg.camera          = PORTERO_PERIPHERAL_UNAVAILABLE;
    msg.microphone      = PORTERO_PERIPHERAL_UNAVAILABLE;
    msg.speaker         = PORTERO_PERIPHERAL_UNAVAILABLE;
    msg.free_heap_bytes = (uint64_t)esp_get_free_heap_size();

    static char buf[TX_BUF_SIZE];
    esp_err_t err = portero_codec_encode_device_status(&msg, buf, sizeof(buf));
    if (err != ESP_OK) return err;

    int n = esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    return (n >= 0) ? ESP_OK : ESP_FAIL;
}

static esp_err_t send_command_ack(const char *command_id,
                                   portero_ack_status_t ack_status,
                                   portero_device_error_code_t error_code)
{
    portero_command_ack_t msg = {0};
    snprintf(msg.boot_id,    sizeof(msg.boot_id),    "%s", s_boot_id);
    snprintf(msg.command_id, sizeof(msg.command_id), "%s", command_id);
    msg.seq        = ++s_seq;
    msg.status     = ack_status;
    msg.error_code = error_code;

    static char buf[TX_BUF_SIZE];
    esp_err_t err = portero_codec_encode_command_ack(&msg, buf, sizeof(buf));
    if (err != ESP_OK) return err;

    int n = esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    return (n >= 0) ? ESP_OK : ESP_FAIL;
}

static void heartbeat_timer_cb(void *arg)
{
    (void)arg;
    if (s_online) {
        send_heartbeat_now();
    }
}

static void handle_message(const char *json)
{
    cJSON *root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGW(TAG, "Failed to parse JSON message");
        return;
    }

    cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (!cJSON_IsString(t) || !t->valuestring) {
        ESP_LOGW(TAG, "Message has no 'type' field");
        cJSON_Delete(root);
        return;
    }

    const char *type = t->valuestring;

    if (strcmp(type, "auth.challenge") == 0) {
        cJSON_Delete(root);
        portero_auth_challenge_t challenge;
        if (portero_codec_decode_auth_challenge(json, &challenge) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to decode auth.challenge");
            return;
        }
        ESP_LOGI(TAG, "auth.challenge received — sending auth.response");
        if (send_auth_response(&challenge) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to send auth.response");
        }

    } else if (strcmp(type, "auth.ok") == 0) {
        cJSON_Delete(root);
        portero_auth_ok_t ok;
        if (portero_codec_decode_auth_ok(json, &ok) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to decode auth.ok");
            return;
        }
        esp_timer_stop(s_hs_timer); /* handshake complete — disarm watchdog */
        s_online = true;
        ESP_LOGI(TAG, "auth.ok — ONLINE (heartbeat every %" PRIu32 " s)",
                 ok.heartbeat_interval_seconds);
        send_device_status();

        esp_timer_start_periodic(s_hb_timer,
                                  (uint64_t)ok.heartbeat_interval_seconds * 1000000ULL);

        if (s_cb) {
            ws_transport_event_t ev = {
                .type                      = WS_TRANSPORT_EVENT_ONLINE,
                .online.heartbeat_interval_s = ok.heartbeat_interval_seconds,
            };
            s_cb(&ev, s_ctx);
        }

    } else if (strcmp(type, "command.request") == 0) {
        cJSON_Delete(root);
        portero_command_request_t cmd;
        if (portero_codec_decode_command_request(json, &cmd) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to decode command.request");
            return;
        }
        ESP_LOGI(TAG, "command.request: cmd=%d id=%s", (int)cmd.command, cmd.command_id);
        send_command_ack(cmd.command_id, PORTERO_ACK_ACCEPTED, PORTERO_ERROR_CODE_NONE);
        if (s_cb) {
            ws_transport_event_t ev = {
                .type    = WS_TRANSPORT_EVENT_COMMAND,
                .command = cmd,
            };
            s_cb(&ev, s_ctx);
        }

    } else {
        ESP_LOGW(TAG, "Unknown message type: %s", type);
        cJSON_Delete(root);
    }
}

/* ---- WebSocket event handler (runs in esp_websocket_client task) ---- */

static void ws_event_handler(void *arg,
                              esp_event_base_t base,
                              int32_t          event_id,
                              void            *event_data)
{
    (void)arg; (void)base;
    esp_websocket_event_data_t *d = event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "Connected — sending device.hello");
        s_online     = false;
        s_seq        = 0;
        s_rx_len     = 0;
        s_rc_attempt = 0; /* successful connect resets backoff */
        esp_timer_stop(s_rc_timer);
        esp_timer_start_once(s_hs_timer, 10000000ULL); /* 10 s handshake watchdog */
        send_hello();
        break;

    case WEBSOCKET_EVENT_DATA:
        if (d->op_code == 0x1 /* TEXT */) {
            if (d->payload_offset == 0) {
                s_rx_len = 0;
            }
            int space = (int)sizeof(s_rx_buf) - 1 - s_rx_len;
            if (d->data_len <= space) {
                memcpy(s_rx_buf + s_rx_len, d->data_ptr, d->data_len);
                s_rx_len += d->data_len;
            } else {
                ESP_LOGE(TAG, "Incoming message too large — discarding");
                s_rx_len = 0;
                break;
            }
            if (s_rx_len == d->payload_len) {
                s_rx_buf[s_rx_len] = '\0';
                handle_message(s_rx_buf);
                s_rx_len = 0;
            }
        }
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_ERROR: {
        uint32_t delay = reconnect_delay_ms(s_rc_attempt);
        ESP_LOGW(TAG, "Disconnected/error — reconnect in %" PRIu32 " ms (attempt %d)",
                 delay, s_rc_attempt);
        s_online = false;
        esp_timer_stop(s_hs_timer);
        esp_timer_stop(s_hb_timer);
        s_rx_len = 0;
        s_rc_attempt++;
        esp_timer_start_once(s_rc_timer, (uint64_t)delay * 1000ULL);
        if (s_cb) {
            ws_transport_event_t ev = { .type = WS_TRANSPORT_EVENT_DISCONNECTED };
            s_cb(&ev, s_ctx);
        }
        break;
    }

    default:
        break;
    }
}

/* ---- Public API ---- */

esp_err_t ws_transport_start(const char *url,
                              const char *device_id,
                              const char *secret,
                              ws_transport_cb_t cb,
                              void *ctx)
{
    if (s_client) return ESP_ERR_INVALID_STATE;

    esp_err_t err = device_auth_generate_boot_id(s_boot_id);
    if (err != ESP_OK) return err;

    snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);
    snprintf(s_secret,    sizeof(s_secret),    "%s", secret);
    s_cb  = cb;
    s_ctx = ctx;

    esp_timer_create_args_t timer_args = {
        .callback = reconnect_timer_cb,
        .name     = "ws_rc",
    };
    err = esp_timer_create(&timer_args, &s_rc_timer);
    if (err != ESP_OK) goto fail_timer;

    timer_args.callback = handshake_timer_cb;
    timer_args.name     = "ws_hs";
    err = esp_timer_create(&timer_args, &s_hs_timer);
    if (err != ESP_OK) goto fail_hs_timer;

    timer_args.callback = heartbeat_timer_cb;
    timer_args.name     = "ws_hb";
    err = esp_timer_create(&timer_args, &s_hb_timer);
    if (err != ESP_OK) goto fail_hb_timer;

    esp_websocket_client_config_t ws_cfg = {
        .uri                  = url,
        .reconnect_timeout_ms = 0,     /* backoff managed by ws_rc timer */
        .network_timeout_ms   = 10000, /* ping response timeout */
        .ping_interval_sec    = 10,    /* WS ping every 10 s — detects silent drops */
        .buffer_size          = RX_BUF_SIZE,
        .task_stack           = 32768,
    };
    s_client = esp_websocket_client_init(&ws_cfg);
    if (!s_client) { err = ESP_ERR_NO_MEM; goto fail_client; }

    esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY,
                                  ws_event_handler, NULL);

    err = esp_websocket_client_start(s_client);
    if (err != ESP_OK) goto fail_start;

    ESP_LOGI(TAG, "Connecting to %s", url);
    return ESP_OK;

fail_start:
    esp_websocket_client_destroy(s_client);
    s_client = NULL;
fail_client:
    esp_timer_delete(s_hb_timer);
    s_hb_timer = NULL;
fail_hb_timer:
    esp_timer_delete(s_hs_timer);
    s_hs_timer = NULL;
fail_hs_timer:
    esp_timer_delete(s_rc_timer);
    s_rc_timer = NULL;
fail_timer:
    device_auth_zeroize(s_secret, sizeof(s_secret));
    return err;
}

esp_err_t ws_transport_send_command_result(const char *command_id,
                                            portero_result_status_t status,
                                            portero_device_error_code_t error_code,
                                            const char *result_json)
{
    if (!s_client || !s_online) return ESP_ERR_INVALID_STATE;

    portero_command_result_t msg = {0};
    snprintf(msg.boot_id,     sizeof(msg.boot_id),     "%s", s_boot_id);
    snprintf(msg.command_id,  sizeof(msg.command_id),  "%s", command_id);
    snprintf(msg.result_json, sizeof(msg.result_json), "%s",
             result_json ? result_json : "{}");
    msg.seq        = ++s_seq;
    msg.status     = status;
    msg.error_code = error_code;

    static char buf[TX_CMD_RESULT_SIZE];
    esp_err_t err = portero_codec_encode_command_result(&msg, buf, sizeof(buf));
    if (err != ESP_OK) return err;

    int n = esp_websocket_client_send_text(s_client, buf, (int)strlen(buf), SEND_TIMEOUT);
    return (n >= 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_transport_stop(void)
{
    if (!s_client) return ESP_OK;

    esp_timer_stop(s_rc_timer);
    esp_timer_delete(s_rc_timer);
    s_rc_timer = NULL;

    esp_timer_stop(s_hs_timer);
    esp_timer_delete(s_hs_timer);
    s_hs_timer = NULL;

    esp_timer_stop(s_hb_timer);
    esp_timer_delete(s_hb_timer);
    s_hb_timer = NULL;

    esp_websocket_client_stop(s_client);
    esp_websocket_client_destroy(s_client);
    s_client = NULL;
    s_online = false;

    device_auth_zeroize(s_secret, sizeof(s_secret));
    return ESP_OK;
}

bool ws_transport_is_online(void)
{
    return s_online;
}
