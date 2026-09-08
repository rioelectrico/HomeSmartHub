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
#define BARGE_IN_RMS_THRESHOLD   300
#define BARGE_IN_FRAMES          3    /* 3 × 20 ms = 60 ms sustained voice */
#define BARGE_IN_COOLDOWN_FRAMES 50   /* 50 × 20 ms = 1 s cooldown */

/* Queue depths */
#define TX_QUEUE_DEPTH  3   /* 3 × 20 ms = 60 ms mic→backend buffering  */
#define SPK_QUEUE_DEPTH 8   /* 8 × 20 ms = 160 ms backend→speaker buffering */

/* PCM frame container (one 20 ms frame at 16 kHz mono) */
typedef struct { int16_t s[BOARD_AUDIO_FRAME_SAMPLES]; } pcm_frame_t;

/* ---- State ---- */

static SemaphoreHandle_t s_mutex         = NULL;
static conv_state_t      s_state         = CONV_STATE_IDLE;
static char              s_stream_id[PORTERO_CODEC_STREAM_ID_BUFFER_SIZE];
static uint8_t           s_stream_id_bin[16]; /* UUID as 16 binary bytes for PAUD header */
static uint64_t          s_tx_seq        = 0;

static QueueHandle_t     s_tx_queue      = NULL; /* mic frames 16 kHz → TX task */
static QueueHandle_t     s_spk_queue     = NULL; /* 16 kHz frames → speaker */
static TaskHandle_t      s_tx_task_h     = NULL;
static volatile bool     s_tx_running    = false;

static volatile int  s_voice_frames   = 0;
static volatile int  s_barge_cooldown = 0;
static volatile bool s_barge_pending  = false;

/* ---- Resampling ---- */

/* 16 kHz → 24 kHz: 320 in → 480 out.
 * Groups of 2 input samples → 3 output via linear interpolation. */
static void resample_16k_to_24k(const int16_t *in, int16_t *out)
{
    for (int i = 0; i < 160; i++) {
        int32_t a = in[i * 2];
        int32_t b = in[i * 2 + 1];
        out[i * 3]     = (int16_t)a;
        out[i * 3 + 1] = (int16_t)((2 * a + b) / 3);
        out[i * 3 + 2] = (int16_t)((a + 2 * b) / 3);
    }
}

/* 24 kHz → 16 kHz: 480 in → 320 out.
 * Groups of 3 input samples → 2 output via simple decimation. */
static void resample_24k_to_16k(const int16_t *in, int16_t *out)
{
    for (int i = 0; i < 160; i++) {
        int32_t x1 = in[i * 3 + 1];
        int32_t x2 = in[i * 3 + 2];
        out[i * 2]     = in[i * 3];
        out[i * 2 + 1] = (int16_t)((x1 + x2) / 2);
    }
}

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
    static pcm_frame_t  frame;
    static int16_t      buf_24k[PAUD_FRAME_SAMPLES]; /* 480 samples */
    static uint8_t      paud_buf[PAUD_FRAME_BYTES];  /* 994 bytes */

    while (s_tx_running) {
        if (xQueueReceive(s_tx_queue, &frame, pdMS_TO_TICKS(50)) != pdTRUE) {
            continue;
        }
        resample_16k_to_24k(frame.s, buf_24k);

        paud_header_t hdr = {
            .type          = PAUD_TYPE_AUDIO,
            .seq           = s_tx_seq++,
            .payload_bytes = PAUD_PAYLOAD_BYTES,
        };
        memcpy(hdr.stream_id, s_stream_id_bin, 16);

        if (paud_encode(&hdr, buf_24k, paud_buf, sizeof(paud_buf)) == ESP_OK) {
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
    xQueueSend(s_tx_queue, &frame, 0);
}

static void speaker_tx_cb(int16_t *buf, size_t count, void *ctx)
{
    (void)ctx;

    /* Barge-in: flush speaker queue and return silence */
    if (s_barge_pending) {
        xQueueReset(s_spk_queue);
        s_barge_pending = false;
        memset(buf, 0, count * sizeof(int16_t));
        return;
    }

    pcm_frame_t frame;
    if (xQueueReceive(s_spk_queue, &frame, 0) == pdTRUE) {
        memcpy(buf, frame.s, count * sizeof(int16_t));
    } else {
        memset(buf, 0, count * sizeof(int16_t)); /* underrun: silence */
    }
}

/* ---- WS binary RX callback (PAUD from backend → speaker queue) ---- */

static void binary_rx_cb(const uint8_t *data, size_t len, void *ctx)
{
    (void)ctx;
    if (s_state != CONV_STATE_ACTIVE) return;

    paud_header_t      hdr;
    const int16_t     *samples_24k;
    static int16_t     buf_16k[BOARD_AUDIO_FRAME_SAMPLES]; /* 320 samples */

    if (paud_decode(data, len, &hdr, &samples_24k) != ESP_OK) return;
    if (hdr.payload_bytes < PAUD_PAYLOAD_BYTES)               return;

    resample_24k_to_16k(samples_24k, buf_16k);

    pcm_frame_t frame;
    memcpy(frame.s, buf_16k, sizeof(frame.s));
    xQueueSend(s_spk_queue, &frame, 0); /* drop if speaker queue full */
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
    memset(s_stream_id, 0, sizeof(s_stream_id));

    ws_transport_set_binary_rx_cb(binary_rx_cb, NULL);
    return ESP_OK;
}

void app_conversation_on_start(const portero_conversation_start_t *msg)
{
    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state == CONV_STATE_ACTIVE) {
        ESP_LOGW(TAG, "conversation.start while already active — restarting");
        s_tx_running = false;
        vTaskDelay(pdMS_TO_TICKS(60)); /* let TX task exit */
        board_audio_stop();
        board_audio_pa_enable(false);
    }

    snprintf(s_stream_id, sizeof(s_stream_id), "%s", msg->stream_id);
    uuid_str_to_bytes(msg->stream_id, s_stream_id_bin);
    s_state          = CONV_STATE_ACTIVE;
    s_tx_seq         = 0;
    s_voice_frames   = 0;
    s_barge_cooldown = 0;
    s_barge_pending  = false;

    xQueueReset(s_tx_queue);
    xQueueReset(s_spk_queue);

    board_audio_set_rx_callback(mic_rx_cb, NULL);
    board_audio_set_tx_callback(speaker_tx_cb, NULL);
    board_audio_start();
    board_audio_pa_enable(true);

    s_tx_running = true;
    xTaskCreate(audio_tx_task, "audio_tx_enc", 4096, NULL, 11, &s_tx_task_h);

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

    s_tx_running = false;
    s_state      = CONV_STATE_IDLE;
    memset(s_stream_id, 0, sizeof(s_stream_id));

    board_audio_stop();
    board_audio_pa_enable(false);
    board_audio_set_rx_callback(NULL, NULL);
    board_audio_set_tx_callback(NULL, NULL);

    xQueueReset(s_tx_queue);
    xQueueReset(s_spk_queue);
    s_tx_task_h = NULL;

    xSemaphoreGive(s_mutex);

    ws_transport_send_conversation_stopped(sid);
    ESP_LOGI(TAG, "IDLE (stopped by backend)");
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
    esp_err_t err = ws_transport_send_ring();
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "device.ring sent");
    } else if (err == ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "portero-ring: not online");
    } else {
        ESP_LOGE(TAG, "portero-ring: send failed %s", esp_err_to_name(err));
    }
}

conv_state_t app_conversation_get_state(void)      { return s_state; }
const char  *app_conversation_get_stream_id(void)   { return s_stream_id; }
bool         app_conversation_is_barge_pending(void)   { return s_barge_pending; }
void         app_conversation_clear_barge_pending(void) { s_barge_pending = false; }
