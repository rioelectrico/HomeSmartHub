#include "app_conversation.h"
#include "board_audio.h"
#include "paud_codec.h"
#include "ws_transport.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "app_conv";

/* Barge-in: sustained voice above RMS threshold triggers speaker flush.
 * Integer comparison (sum_sq/N > threshold²) avoids float sqrt. */
#define BARGE_IN_RMS_THRESHOLD   2000  /* raised to avoid false triggers from speaker leakage */
#define BARGE_IN_FRAMES          5     /* 5 × 20 ms = 100 ms sustained voice */
#define BARGE_IN_COOLDOWN_FRAMES 150   /* 150 × 20 ms = 3 s cooldown */

/* Queue depths */
#define TX_QUEUE_DEPTH  8    /* 8 × 20 ms = 160 ms mic→backend buffering   */
#define SPK_QUEUE_DEPTH 256  /* 256 × 20 ms = 5 s — absorbs backend bursts */

/* PCM frame container (one 20 ms frame at 16 kHz mono) */
typedef struct { int16_t s[BOARD_AUDIO_FRAME_SAMPLES]; } pcm_frame_t;

/* ---- State ---- */

static SemaphoreHandle_t s_mutex              = NULL;
static conv_state_t      s_state              = CONV_STATE_IDLE;
static char              s_stream_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
static char              s_conversation_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
static uint8_t           s_stream_id_bin[16]; /* UUID as 16 binary bytes for PAUD header */
static uint64_t          s_tx_seq             = 0;

static QueueHandle_t     s_tx_queue      = NULL; /* mic frames 16 kHz → TX task */
static QueueHandle_t     s_spk_queue     = NULL; /* 16 kHz frames → speaker */
static TaskHandle_t      s_tx_task_h     = NULL;
static volatile bool     s_tx_running    = false;

static volatile int  s_voice_frames   = 0;
static volatile int  s_barge_cooldown = 0;
static volatile bool s_barge_pending  = false;

static int16_t s_last_spk_sample = 0; /* for click-free fades */

/* Telemetry counters — reset at conversation start, logged every 5s */
static volatile uint32_t s_dbg_spk_rx       = 0; /* frames received from backend */
static volatile uint32_t s_dbg_spk_overflow = 0; /* frames dropped (spk queue full) */
static volatile uint32_t s_dbg_spk_underrun = 0; /* times speaker found queue empty */
static volatile uint32_t s_dbg_mic_tx       = 0; /* frames sent to backend */
static volatile uint32_t s_dbg_mic_drop     = 0; /* frames dropped (tx queue full) */
static volatile uint32_t s_dbg_tick         = 0; /* mic callback count, for 5s log */

/* Parse "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" into 16 binary bytes. */
static void uuid_str_to_bytes(const char *uuid, uint8_t *out)
{
    int i = 0;
    while (*uuid && i < 16) {
        if (*uuid == '-') { uuid++; continue; }
        uint8_t hi = (*uuid >= 'a') ? (uint8_t)(*uuid - 'a' + 10) : (uint8_t)(*uuid - '0');
        uuid++;
        uint8_t lo = (*uuid >= 'a') ? (uint8_t)(*uuid - 'a' + 10) : (uint8_t)(*uuid - '0');
        uuid++;
        out[i++] = (uint8_t)((hi << 4) | lo);
    }
}

/* ---- Audio TX task (mic frames → PAUD → backend) ---- */

static void audio_tx_task(void *arg)
{
    (void)arg;
    static pcm_frame_t frame;
    static uint8_t     paud_buf[PAUD_FRAME_BYTES]; /* 994 bytes */

    while (s_tx_running) {
        if (xQueueReceive(s_tx_queue, &frame, pdMS_TO_TICKS(50)) != pdTRUE) {
            continue;
        }
        /* board_audio @ 24 kHz: frame.s already has PAUD_FRAME_SAMPLES (480) samples */
        paud_header_t hdr = {
            .type          = PAUD_TYPE_AUDIO,
            .seq           = s_tx_seq++,
            .payload_bytes = PAUD_PAYLOAD_BYTES,
        };
        memcpy(hdr.stream_id, s_stream_id_bin, 16);

        if (paud_encode(&hdr, frame.s, paud_buf, sizeof(paud_buf)) == ESP_OK) {
            ws_transport_send_audio_frame(paud_buf, PAUD_FRAME_BYTES);
        }
    }
    vTaskDelete(NULL);
}

/* ---- board_audio callbacks ---- */

static void mic_rx_cb(const int16_t *samples, size_t count, void *ctx)
{
    (void)ctx;

    /* Barge-in detection */
    if (s_barge_cooldown > 0) {
        s_barge_cooldown--;
    } else {
        int64_t sum_sq = 0;
        for (size_t i = 0; i < count; i++) {
            int32_t v = samples[i];
            sum_sq += (int64_t)v * v;
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

    /* Enqueue for TX pipeline — non-blocking, drop frame if queue full */
    pcm_frame_t frame;
    memcpy(frame.s, samples, count * sizeof(int16_t));
    if (xQueueSend(s_tx_queue, &frame, 0) == pdTRUE) {
        s_dbg_mic_tx++;
    } else {
        s_dbg_mic_drop++;
    }

    /* Log telemetry every 250 mic callbacks ≈ 5 s */
    if (++s_dbg_tick >= 250) {
        s_dbg_tick = 0;
        UBaseType_t spk_depth = uxQueueMessagesWaiting(s_spk_queue);
        ESP_LOGI(TAG, "[audio-dbg] spk: rx=%lu ovfl=%lu undr=%lu q=%u | mic: tx=%lu drop=%lu",
                 (unsigned long)s_dbg_spk_rx, (unsigned long)s_dbg_spk_overflow,
                 (unsigned long)s_dbg_spk_underrun, (unsigned)spk_depth,
                 (unsigned long)s_dbg_mic_tx, (unsigned long)s_dbg_mic_drop);
    }
}

static void speaker_tx_cb(int16_t *buf, size_t count, void *ctx)
{
    (void)ctx;

    /* Barge-in: flush speaker queue, fade out to avoid click */
    if (s_barge_pending) {
        xQueueReset(s_spk_queue);
        s_barge_pending = false;
        for (size_t i = 0; i < count; i++) {
            buf[i] = (int16_t)((int32_t)s_last_spk_sample * (int32_t)(count - i) / (int32_t)count);
        }
        s_last_spk_sample = 0;
        return;
    }

    pcm_frame_t frame;
    if (xQueueReceive(s_spk_queue, &frame, 0) == pdTRUE) {
        memcpy(buf, frame.s, count * sizeof(int16_t));
        s_last_spk_sample = frame.s[count - 1];
    } else {
        s_dbg_spk_underrun++;
        /* Underrun: fade to silence rather than abrupt zero */
        for (size_t i = 0; i < count; i++) {
            buf[i] = (int16_t)((int32_t)s_last_spk_sample * (int32_t)(count - i) / (int32_t)count);
        }
        s_last_spk_sample = 0;
    }
}

/* ---- WS binary RX callback (PAUD from backend → speaker queue) ---- */

static void binary_rx_cb(const uint8_t *data, size_t len, void *ctx)
{
    (void)ctx;
    if (s_state != CONV_STATE_ACTIVE) return;

    paud_header_t  hdr;
    const int16_t *samples_24k;

    if (paud_decode(data, len, &hdr, &samples_24k) != ESP_OK) return;
    if (hdr.payload_bytes < PAUD_PAYLOAD_BYTES)               return;

    /* board_audio @ 24 kHz: copy PAUD payload directly — no resampling */
    pcm_frame_t frame;
    memcpy(frame.s, samples_24k, sizeof(frame.s));
    s_dbg_spk_rx++;
    if (xQueueSend(s_spk_queue, &frame, 0) != pdTRUE) {
        s_dbg_spk_overflow++;
    }
}

/* ---- Public API ---- */

esp_err_t app_conversation_init(void)
{
    s_mutex = xSemaphoreCreateMutex();
    if (!s_mutex) return ESP_ERR_NO_MEM;

    s_tx_queue  = xQueueCreate(TX_QUEUE_DEPTH,  sizeof(pcm_frame_t));
    s_spk_queue = xQueueCreate(SPK_QUEUE_DEPTH, sizeof(pcm_frame_t));
    if (!s_tx_queue || !s_spk_queue) return ESP_ERR_NO_MEM;

    s_state = CONV_STATE_IDLE;
    memset(s_stream_id,       0, sizeof(s_stream_id));
    memset(s_conversation_id, 0, sizeof(s_conversation_id));

    ws_transport_set_binary_rx_cb(binary_rx_cb, NULL);
    return ESP_OK;
}

void app_conversation_on_started(const portero_conversation_started_t *msg)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state == CONV_STATE_ACTIVE) {
        ESP_LOGW(TAG, "conversation.started while already active — restarting");
        s_tx_running = false;
        vTaskDelay(pdMS_TO_TICKS(60));
        board_audio_stop();
        board_audio_pa_enable(false);
    } else if (s_state != CONV_STATE_STARTING) {
        ESP_LOGW(TAG, "conversation.started unexpected (state=%d) — ignoring", (int)s_state);
        xSemaphoreGive(s_mutex);
        return;
    }

    snprintf(s_stream_id,       sizeof(s_stream_id),       "%s", msg->stream_id);
    snprintf(s_conversation_id, sizeof(s_conversation_id), "%s", msg->conversation_id);
    uuid_str_to_bytes(msg->stream_id, s_stream_id_bin);
    s_state          = CONV_STATE_ACTIVE;
    s_tx_seq          = 1; /* backend rejects seq=0; PAUD sequences start at 1 */
    s_voice_frames    = 0;
    s_barge_cooldown  = 0;
    s_barge_pending   = false;
    s_last_spk_sample = 0;

    s_dbg_spk_rx       = 0;
    s_dbg_spk_overflow = 0;
    s_dbg_spk_underrun = 0;
    s_dbg_mic_tx       = 0;
    s_dbg_mic_drop     = 0;
    s_dbg_tick         = 0;

    xQueueReset(s_tx_queue);
    xQueueReset(s_spk_queue);

    board_audio_set_rx_callback(mic_rx_cb, NULL);
    board_audio_set_tx_callback(speaker_tx_cb, NULL);
    board_audio_start();
    board_audio_pa_enable(true);

    s_tx_running = true;
    xTaskCreate(audio_tx_task, "audio_tx_enc", 8192, NULL, 8, &s_tx_task_h);

    xSemaphoreGive(s_mutex);
    ESP_LOGI(TAG, "ACTIVE conv=%s stream=%s", msg->conversation_id, msg->stream_id);
}

void app_conversation_on_ended(const portero_conversation_ended_t *msg)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state == CONV_STATE_IDLE) {
        ESP_LOGW(TAG, "conversation.ended while IDLE");
        xSemaphoreGive(s_mutex);
        return;
    }

    s_tx_running = false;
    s_state      = CONV_STATE_IDLE;
    memset(s_stream_id,       0, sizeof(s_stream_id));
    memset(s_conversation_id, 0, sizeof(s_conversation_id));

    board_audio_stop();
    board_audio_pa_enable(false);
    board_audio_set_rx_callback(NULL, NULL);
    board_audio_set_tx_callback(NULL, NULL);

    xQueueReset(s_tx_queue);
    xQueueReset(s_spk_queue);
    s_tx_task_h = NULL;

    xSemaphoreGive(s_mutex);
    ESP_LOGI(TAG, "IDLE (ended by backend, outcome=%s)", msg ? msg->outcome : "?");
}

void app_conversation_on_audio_clear(const portero_conversation_audio_clear_t *msg)
{
    (void)msg;
    if (s_state != CONV_STATE_ACTIVE) return;
    s_barge_pending = true;
    ESP_LOGD(TAG, "audio.clear — speaker flush");
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
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (s_state != CONV_STATE_IDLE) {
        ESP_LOGW(TAG, "portero-ring: already in state %d", (int)s_state);
        xSemaphoreGive(s_mutex);
        return;
    }
    s_state = CONV_STATE_STARTING;
    xSemaphoreGive(s_mutex);

    esp_err_t err = ws_transport_send_conversation_start();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "conversation.start sent — waiting for backend");
    } else {
        xSemaphoreTake(s_mutex, portMAX_DELAY);
        s_state = CONV_STATE_IDLE;
        xSemaphoreGive(s_mutex);
        if (err == ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "portero-ring: not online");
        } else {
            ESP_LOGE(TAG, "portero-ring: send failed %s", esp_err_to_name(err));
        }
    }
}

void app_conversation_on_error(const portero_conversation_error_t *msg)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (s_state == CONV_STATE_IDLE) {
        xSemaphoreGive(s_mutex);
        ESP_LOGW(TAG, "conversation.error while IDLE — code=%s", msg ? msg->code : "?");
        return;
    }
    bool was_active = (s_state == CONV_STATE_ACTIVE);
    s_tx_running = false;
    s_state      = CONV_STATE_IDLE;
    memset(s_stream_id, 0, sizeof(s_stream_id));
    xSemaphoreGive(s_mutex);

    if (was_active) {
        board_audio_stop();
        board_audio_pa_enable(false);
        board_audio_set_rx_callback(NULL, NULL);
        board_audio_set_tx_callback(NULL, NULL);
        xQueueReset(s_tx_queue);
        xQueueReset(s_spk_queue);
        s_tx_task_h = NULL;
    }
    ESP_LOGE(TAG, "conversation.error code=%s — reset to IDLE", msg ? msg->code : "?");
}

void app_conversation_on_disconnect(void)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if (s_state == CONV_STATE_IDLE) {
        xSemaphoreGive(s_mutex);
        return;
    }
    bool was_active = (s_state == CONV_STATE_ACTIVE);
    s_tx_running = false;
    s_state      = CONV_STATE_IDLE;
    memset(s_stream_id,       0, sizeof(s_stream_id));
    memset(s_conversation_id, 0, sizeof(s_conversation_id));
    xSemaphoreGive(s_mutex);

    if (was_active) {
        board_audio_stop();
        board_audio_pa_enable(false);
        board_audio_set_rx_callback(NULL, NULL);
        board_audio_set_tx_callback(NULL, NULL);
        xQueueReset(s_tx_queue);
        xQueueReset(s_spk_queue);
        s_tx_task_h = NULL;
    }
    ESP_LOGW(TAG, "WS disconnected — reset to IDLE");
}

conv_state_t app_conversation_get_state(void)      { return s_state; }
const char  *app_conversation_get_stream_id(void)   { return s_stream_id; }
bool         app_conversation_is_barge_pending(void)   { return s_barge_pending; }
void         app_conversation_clear_barge_pending(void) { s_barge_pending = false; }
