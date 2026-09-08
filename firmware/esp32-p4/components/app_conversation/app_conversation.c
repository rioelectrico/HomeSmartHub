#include "app_conversation.h"
#include "board_audio.h"
#include "ws_transport.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "app_conv";

/* Barge-in: BARGE_IN_FRAMES consecutive frames above threshold → trigger.
 * Uses integer comparison (sum_sq/count > threshold²) to avoid float sqrt. */
#define BARGE_IN_RMS_THRESHOLD   300
#define BARGE_IN_FRAMES          3    /* 3 × 20 ms = 60 ms sustained voice */
#define BARGE_IN_COOLDOWN_FRAMES 50   /* 50 × 20 ms = 1 s cooldown after trigger */

static SemaphoreHandle_t s_mutex         = NULL;
static conv_state_t      s_state         = CONV_STATE_IDLE;
static char              s_stream_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];

static volatile int  s_voice_frames   = 0;
static volatile int  s_barge_cooldown = 0;
static volatile bool s_barge_pending  = false;

/* ---- board_audio callbacks ---- */

static void mic_rx_cb(const int16_t *samples, size_t count, void *ctx)
{
    (void)ctx;

    if (s_barge_cooldown > 0) {
        s_barge_cooldown--;
        return;
    }

    /* Integer RMS: compare sum_sq/count > threshold² (no float needed) */
    int64_t sum_sq = 0;
    for (size_t i = 0; i < count; i++) {
        int32_t s = samples[i];
        sum_sq += (int64_t)s * s;
    }
    bool loud = (sum_sq / (int64_t)count) >
                ((int64_t)BARGE_IN_RMS_THRESHOLD * BARGE_IN_RMS_THRESHOLD);

    if (loud) {
        if (++s_voice_frames >= BARGE_IN_FRAMES) {
            s_voice_frames = 0;
            app_conversation_barge_in();
        }
    } else {
        s_voice_frames = 0;
    }
}

static void speaker_tx_cb(int16_t *buf, size_t count, void *ctx)
{
    (void)ctx;
    /* FW2-12 will replace this with real audio from backend.
     * Until then, output silence so the I2S TX task keeps running. */
    memset(buf, 0, count * sizeof(int16_t));
}

/* ---- Public API ---- */

esp_err_t app_conversation_init(void)
{
    s_mutex = xSemaphoreCreateMutex();
    if (!s_mutex) return ESP_ERR_NO_MEM;
    s_state = CONV_STATE_IDLE;
    memset(s_stream_id, 0, sizeof(s_stream_id));
    return ESP_OK;
}

void app_conversation_on_start(const portero_conversation_start_t *msg)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state == CONV_STATE_ACTIVE) {
        ESP_LOGW(TAG, "conversation.start while already active — restarting");
        board_audio_stop();
        board_audio_pa_enable(false);
    }

    snprintf(s_stream_id, sizeof(s_stream_id), "%s", msg->stream_id);
    s_state          = CONV_STATE_ACTIVE;
    s_voice_frames   = 0;
    s_barge_cooldown = 0;
    s_barge_pending  = false;

    board_audio_set_rx_callback(mic_rx_cb, NULL);
    board_audio_set_tx_callback(speaker_tx_cb, NULL);
    board_audio_start();
    board_audio_pa_enable(true);

    xSemaphoreGive(s_mutex);

    ws_transport_send_conversation_started(msg->stream_id);
    ESP_LOGI(TAG, "ACTIVE stream=%s", msg->stream_id);
}

void app_conversation_on_stop(const portero_conversation_stop_t *msg)
{
    (void)msg;
    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state != CONV_STATE_ACTIVE) {
        ESP_LOGW(TAG, "conversation.stop while not active");
        xSemaphoreGive(s_mutex);
        return;
    }

    char sid[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
    snprintf(sid, sizeof(sid), "%s", s_stream_id);

    s_state = CONV_STATE_IDLE;
    memset(s_stream_id, 0, sizeof(s_stream_id));

    board_audio_stop();
    board_audio_pa_enable(false);
    board_audio_set_rx_callback(NULL, NULL);
    board_audio_set_tx_callback(NULL, NULL);

    xSemaphoreGive(s_mutex);

    ws_transport_send_conversation_stopped(sid);
    ESP_LOGI(TAG, "IDLE (stopped by backend)");
}

void app_conversation_on_audio_clear(const portero_conversation_audio_clear_t *msg)
{
    (void)msg;
    if (s_state != CONV_STATE_ACTIVE) return;
    s_barge_pending = true;
    ESP_LOGD(TAG, "audio.clear — speaker flush requested");
}

void app_conversation_barge_in(void)
{
    if (s_state != CONV_STATE_ACTIVE) return;
    s_barge_pending  = true;
    s_barge_cooldown = BARGE_IN_COOLDOWN_FRAMES;
    ESP_LOGI(TAG, "barge-in — flushing speaker");
}

void app_conversation_ring(void)
{
    esp_err_t err = ws_transport_send_ring();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "device.ring sent");
    } else if (err == ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "portero-ring: not online");
    } else {
        ESP_LOGE(TAG, "portero-ring: send failed %s", esp_err_to_name(err));
    }
}

conv_state_t app_conversation_get_state(void)     { return s_state; }
const char  *app_conversation_get_stream_id(void)  { return s_stream_id; }
bool         app_conversation_is_barge_pending(void)  { return s_barge_pending; }
void         app_conversation_clear_barge_pending(void) { s_barge_pending = false; }
