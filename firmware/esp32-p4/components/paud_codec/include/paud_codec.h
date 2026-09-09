#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* PAUD wire protocol constants */
#define PAUD_VERSION         1
#define PAUD_HEADER_SIZE     34
#define PAUD_SAMPLE_RATE_HZ  24000
#define PAUD_FRAME_SAMPLES   480               /* 20 ms @ 24 kHz mono */
#define PAUD_PAYLOAD_BYTES   960               /* PAUD_FRAME_SAMPLES × sizeof(int16_t) */
#define PAUD_FRAME_BYTES     (PAUD_HEADER_SIZE + PAUD_PAYLOAD_BYTES)  /* 994 */

typedef enum {
    PAUD_TYPE_AUDIO = 0x01,
} paud_type_t;

typedef struct {
    uint8_t  type;           /* paud_type_t */
    uint8_t  stream_id[16];  /* opaque 16-byte stream UUID */
    uint64_t seq;            /* frame sequence number */
    uint32_t payload_bytes;  /* payload length (always PAUD_PAYLOAD_BYTES for audio) */
} paud_header_t;

/**
 * Encode one PAUD audio frame into out_buf.
 * out_buf must be >= PAUD_FRAME_BYTES (994) bytes.
 * samples must be PAUD_FRAME_SAMPLES (480) PCM16-LE mono @ 24 kHz samples.
 */
esp_err_t paud_encode(const paud_header_t *hdr,
                      const int16_t       *samples,
                      uint8_t             *out_buf,
                      size_t               out_size);

/**
 * Decode a PAUD frame from buf (at least PAUD_FRAME_BYTES bytes).
 * On success: *out_hdr is populated, *out_samples points into buf (zero-copy).
 * Returns ESP_ERR_INVALID_RESPONSE on bad magic/version.
 */
esp_err_t paud_decode(const uint8_t    *buf,
                      size_t            len,
                      paud_header_t    *out_hdr,
                      const int16_t   **out_samples);

#ifdef __cplusplus
}
#endif
