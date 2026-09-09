#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * board_audio — ES8311 + NS4150B audio layer for Waveshare ESP32-P4-ETH.
 *
 * GPIO ownership (all defined here, never referenced outside this component):
 *   I2C SDA  GPIO7   I2C SCL  GPIO8
 *   I2S DOUT GPIO9   I2S WS   GPIO10   I2S DIN  GPIO11
 *   I2S BCLK GPIO12  I2S MCLK GPIO13
 *   PA EN    GPIO53
 *
 * I2S runs stereo 16-bit @ 24 kHz internally (matches PAUD stream rate — no resampling).
 * Capture delivers mono (left channel only).
 * Playback accepts mono (duplicated to L+R for TX).
 */

/* Native sample rate — matches PAUD (24 kHz), no resampling required */
#define BOARD_AUDIO_SAMPLE_RATE_HZ   24000
/* Samples per 20 ms frame at BOARD_AUDIO_SAMPLE_RATE_HZ (mono) */
#define BOARD_AUDIO_FRAME_SAMPLES    480

/*
 * Called when the I2S RX DMA delivers a frame.
 * `samples` points to BOARD_AUDIO_FRAME_SAMPLES mono int16 PCM samples.
 * Must not block; copy or enqueue immediately.
 */
typedef void (*board_audio_rx_cb_t)(const int16_t *samples,
                                    size_t         count,
                                    void          *ctx);

/*
 * Called when the I2S TX DMA needs the next frame.
 * Fill `samples` with BOARD_AUDIO_FRAME_SAMPLES mono int16 PCM samples.
 * Write silence (zeros) if no audio is ready. Must not block.
 */
typedef void (*board_audio_tx_fill_cb_t)(int16_t *samples,
                                         size_t   count,
                                         void    *ctx);

/**
 * Initialize I2C bus, ES8311 codec, and I2S full-duplex channel.
 * PA amplifier (GPIO53) is left disabled after init.
 * Returns ESP_ERR_INVALID_STATE if already initialized.
 */
esp_err_t board_audio_init(void);

/** Stop I2S, disable PA, release codec and I2C resources. */
esp_err_t board_audio_deinit(void);

/**
 * Register RX callback (capture). Must be called before board_audio_start().
 * Pass cb=NULL to unregister.
 */
esp_err_t board_audio_set_rx_callback(board_audio_rx_cb_t cb, void *ctx);

/**
 * Register TX fill callback (playback). Must be called before board_audio_start().
 * Pass cb=NULL to unregister (TX will output silence).
 */
esp_err_t board_audio_set_tx_callback(board_audio_tx_fill_cb_t cb, void *ctx);

/**
 * Start the I2S RX/TX tasks. board_audio_init() must have been called first.
 * Returns ESP_ERR_INVALID_STATE if not initialized or already started.
 */
esp_err_t board_audio_start(void);

/** Stop the I2S RX/TX tasks. PA is disabled before returning. */
esp_err_t board_audio_stop(void);

/**
 * Enable or disable the NS4150B power amplifier (GPIO53).
 * Requires board_audio_init() to have been called.
 */
esp_err_t board_audio_pa_enable(bool enable);

/** Returns true after board_audio_init() and before board_audio_deinit(). */
bool board_audio_is_initialized(void);

/** Returns true after board_audio_start() and before board_audio_stop(). */
bool board_audio_is_running(void);

#ifdef __cplusplus
}
#endif
