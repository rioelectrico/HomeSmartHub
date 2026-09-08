#include "board_audio.h"
#include "hardware_hal.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "es8311_codec.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "board_audio";

/* ---- GPIO ---- */
#define BOARD_I2C_SDA_IO    7
#define BOARD_I2C_SCL_IO    8
#define BOARD_I2S_DOUT_IO   9
#define BOARD_I2S_WS_IO     10
#define BOARD_I2S_DIN_IO    11
#define BOARD_I2S_BCLK_IO   12
#define BOARD_I2S_MCLK_IO   13
#define BOARD_PA_EN_IO      53

#define BOARD_ES8311_I2C_ADDR  0x30  /* 8-bit write addr; library does addr>>1 internally */
#define BOARD_I2C_PORT         I2C_NUM_0
#define BOARD_I2S_PORT         I2S_NUM_0
/* MCLK = SAMPLE_RATE × 384 = 16000 × 384 = 6.144 MHz */
#define BOARD_MCLK_MULTIPLE    I2S_MCLK_MULTIPLE_384

/* DMA: 2 buffers × 640 bytes each (320 stereo int16 samples = one 20 ms frame) */
#define BOARD_DMA_BUF_COUNT  2
#define BOARD_DMA_BUF_FRAMES 1
#define BOARD_DMA_BUF_LEN    (BOARD_AUDIO_FRAME_SAMPLES * 2 * sizeof(int16_t)) /* stereo */

/* Task sizes */
#define BOARD_RX_TASK_STACK  4096
#define BOARD_TX_TASK_STACK  4096
#define BOARD_RX_TASK_PRIO   10
#define BOARD_TX_TASK_PRIO   10

/* ---- State ---- */

static i2c_master_bus_handle_t s_i2c_bus    = NULL;
static esp_codec_dev_handle_t  s_codec      = NULL;
static i2s_chan_handle_t        s_i2s_tx     = NULL;
static i2s_chan_handle_t        s_i2s_rx     = NULL;
static TaskHandle_t             s_rx_task    = NULL;
static TaskHandle_t             s_tx_task    = NULL;

static board_audio_rx_cb_t      s_rx_cb      = NULL;
static void                    *s_rx_ctx     = NULL;
static board_audio_tx_fill_cb_t s_tx_cb      = NULL;
static void                    *s_tx_ctx     = NULL;

static volatile bool s_initialized = false;
static volatile bool s_running     = false;

/* Preallocated frame buffers — no malloc per frame */
static int16_t s_rx_stereo_buf[BOARD_AUDIO_FRAME_SAMPLES * 2]; /* L+R */
static int16_t s_rx_mono_buf[BOARD_AUDIO_FRAME_SAMPLES];
static int16_t s_tx_mono_buf[BOARD_AUDIO_FRAME_SAMPLES];
static int16_t s_tx_stereo_buf[BOARD_AUDIO_FRAME_SAMPLES * 2]; /* L+R */

/* ---- Internal helpers ---- */

static void pa_gpio_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << BOARD_PA_EN_IO),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    gpio_set_level(BOARD_PA_EN_IO, 0); /* PA off at init */
}

/* Extract left channel from interleaved L/R stereo */
static void stereo_to_mono_left(const int16_t *stereo, int16_t *mono, size_t frames)
{
    for (size_t i = 0; i < frames; i++) {
        mono[i] = stereo[i * 2];
    }
}

/* Duplicate mono to interleaved L+R stereo */
static void mono_to_stereo(const int16_t *mono, int16_t *stereo, size_t frames)
{
    for (size_t i = 0; i < frames; i++) {
        stereo[i * 2]     = mono[i];
        stereo[i * 2 + 1] = mono[i];
    }
}

/* ---- I2S tasks ---- */

static void rx_task(void *arg)
{
    size_t bytes_read = 0;

    while (s_running) {
        esp_err_t err = i2s_channel_read(s_i2s_rx,
                                          s_rx_stereo_buf,
                                          sizeof(s_rx_stereo_buf),
                                          &bytes_read,
                                          pdMS_TO_TICKS(100));
        if (err != ESP_OK || bytes_read == 0) {
            continue;
        }
        stereo_to_mono_left(s_rx_stereo_buf, s_rx_mono_buf, BOARD_AUDIO_FRAME_SAMPLES);
        if (s_rx_cb) {
            s_rx_cb(s_rx_mono_buf, BOARD_AUDIO_FRAME_SAMPLES, s_rx_ctx);
        }
    }
    vTaskDelete(NULL);
}

static void tx_task(void *arg)
{
    size_t bytes_written = 0;

    while (s_running) {
        if (s_tx_cb) {
            s_tx_cb(s_tx_mono_buf, BOARD_AUDIO_FRAME_SAMPLES, s_tx_ctx);
        } else {
            memset(s_tx_mono_buf, 0, sizeof(s_tx_mono_buf));
        }
        mono_to_stereo(s_tx_mono_buf, s_tx_stereo_buf, BOARD_AUDIO_FRAME_SAMPLES);
        i2s_channel_write(s_i2s_tx,
                          s_tx_stereo_buf,
                          sizeof(s_tx_stereo_buf),
                          &bytes_written,
                          pdMS_TO_TICKS(100));
    }
    vTaskDelete(NULL);
}

/* ---- Public API ---- */

esp_err_t board_audio_init(void)
{
    if (s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    pa_gpio_init();

    /* I2C master bus */
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port            = BOARD_I2C_PORT,
        .sda_io_num          = BOARD_I2C_SDA_IO,
        .scl_io_num          = BOARD_I2C_SCL_IO,
        .clk_source          = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt   = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_i2c_bus),
                        TAG, "I2C master bus init failed");

    /* I2S full-duplex — create channels BEFORE codec so handles are valid */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(BOARD_I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx),
                        TAG, "I2S new channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_AUDIO_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                         I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK_IO,
            .bclk = BOARD_I2S_BCLK_IO,
            .ws   = BOARD_I2S_WS_IO,
            .dout = BOARD_I2S_DOUT_IO,
            .din  = BOARD_I2S_DIN_IO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.clk_cfg.mclk_multiple = BOARD_MCLK_MULTIPLE;

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg),
                        TAG, "I2S TX init failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg),
                        TAG, "I2S RX init failed");

    /* ES8311 via esp_codec_dev — now that I2S handles are valid */
    audio_codec_i2s_cfg_t i2s_data_cfg = {
        .port      = BOARD_I2S_PORT,
        .rx_handle = s_i2s_rx,
        .tx_handle = s_i2s_tx,
    };
    audio_codec_i2c_cfg_t i2c_cfg = {
        .addr       = BOARD_ES8311_I2C_ADDR,
        .bus_handle = s_i2c_bus,
    };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_data_cfg);
    const audio_codec_ctrl_if_t *ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();

    es8311_codec_cfg_t es8311_cfg = {
        .ctrl_if     = ctrl_if,
        .gpio_if     = gpio_if,
        .codec_mode  = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin      = -1,       /* PA managed separately via GPIO53 */
        .pa_reverted = false,
        .master_mode = false,    /* ESP32-P4 is I2S master; ES8311 is slave */
        .use_mclk    = true,
    };
    s_codec = esp_codec_dev_new(&(esp_codec_dev_cfg_t){
        .dev_type = ESP_CODEC_DEV_TYPE_IN_OUT,
        .codec_if = es8311_codec_new(&es8311_cfg),
        .data_if  = data_if,
    });
    if (!s_codec) {
        ESP_LOGE(TAG, "esp_codec_dev_new failed");
        i2s_del_channel(s_i2s_tx);
        i2s_del_channel(s_i2s_rx);
        s_i2s_tx = s_i2s_rx = NULL;
        i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
        return ESP_FAIL;
    }

    /* Open codec (configures ES8311 registers via I2C; I2S not enabled yet) */
    esp_codec_dev_sample_info_t fs = {
        .sample_rate     = BOARD_AUDIO_SAMPLE_RATE_HZ,
        .channel         = 2,
        .bits_per_sample = 16,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_set_out_vol(s_codec, 70),
                        TAG, "set output volume failed");
    ESP_RETURN_ON_ERROR(esp_codec_dev_set_in_gain(s_codec, 24.0f),
                        TAG, "set input gain failed");
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(s_codec, &fs),
                        TAG, "codec open failed");

    s_initialized = true;
    ESP_LOGI(TAG, "board_audio init OK — ES8311 @ 0x%02X, I2S %d Hz stereo 16-bit",
             BOARD_ES8311_I2C_ADDR, BOARD_AUDIO_SAMPLE_RATE_HZ);
    return ESP_OK;
}

esp_err_t board_audio_deinit(void)
{
    if (!s_initialized) return ESP_OK;
    if (s_running) {
        board_audio_stop();
    }

    board_audio_pa_enable(false);

    if (s_codec) {
        esp_codec_dev_close(s_codec);
        esp_codec_dev_delete(s_codec);
        s_codec = NULL;
    }
    if (s_i2s_tx) {
        i2s_del_channel(s_i2s_tx);
        s_i2s_tx = NULL;
    }
    if (s_i2s_rx) {
        i2s_del_channel(s_i2s_rx);
        s_i2s_rx = NULL;
    }
    if (s_i2c_bus) {
        i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
    }

    s_initialized = false;
    ESP_LOGI(TAG, "board_audio deinit");
    return ESP_OK;
}

esp_err_t board_audio_set_rx_callback(board_audio_rx_cb_t cb, void *ctx)
{
    s_rx_cb  = cb;
    s_rx_ctx = ctx;
    return ESP_OK;
}

esp_err_t board_audio_set_tx_callback(board_audio_tx_fill_cb_t cb, void *ctx)
{
    s_tx_cb  = cb;
    s_tx_ctx = ctx;
    return ESP_OK;
}

esp_err_t board_audio_start(void)
{
    if (!s_initialized) return ESP_ERR_INVALID_STATE;
    if (s_running)      return ESP_ERR_INVALID_STATE;

    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_tx), TAG, "I2S TX enable failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG, "I2S RX enable failed");

    s_running = true;

    xTaskCreate(rx_task, "audio_rx", BOARD_RX_TASK_STACK, NULL, BOARD_RX_TASK_PRIO, &s_rx_task);
    xTaskCreate(tx_task, "audio_tx", BOARD_TX_TASK_STACK, NULL, BOARD_TX_TASK_PRIO, &s_tx_task);

    ESP_LOGI(TAG, "board_audio started");
    return ESP_OK;
}

esp_err_t board_audio_stop(void)
{
    if (!s_running) return ESP_OK;

    s_running = false;

    /* Tasks check s_running and exit; give them time to stop */
    vTaskDelay(pdMS_TO_TICKS(50));

    i2s_channel_disable(s_i2s_tx);
    i2s_channel_disable(s_i2s_rx);

    board_audio_pa_enable(false);

    s_rx_task = NULL;
    s_tx_task = NULL;

    ESP_LOGI(TAG, "board_audio stopped");
    return ESP_OK;
}

esp_err_t board_audio_pa_enable(bool enable)
{
    if (!s_initialized) return ESP_ERR_INVALID_STATE;
    gpio_set_level(BOARD_PA_EN_IO, enable ? 1 : 0);
    ESP_LOGD(TAG, "PA %s", enable ? "ON" : "OFF");
    return ESP_OK;
}

bool board_audio_is_initialized(void) { return s_initialized; }
bool board_audio_is_running(void)     { return s_running; }
