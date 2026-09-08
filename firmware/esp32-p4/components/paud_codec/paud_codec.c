#include "paud_codec.h"
#include <string.h>
#include "esp_log.h"

static const char *TAG = "paud_codec";

static const uint8_t MAGIC[4] = {'P', 'A', 'U', 'D'};

static inline void write_u64_be(uint8_t *p, uint64_t v)
{
    p[0] = (uint8_t)(v >> 56); p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40); p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24); p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >>  8); p[7] = (uint8_t)(v);
}

static inline void write_u32_be(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >>  8); p[3] = (uint8_t)(v);
}

static inline uint64_t read_u64_be(const uint8_t *p)
{
    return ((uint64_t)p[0] << 56) | ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) | ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) | ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] <<  8) |  (uint64_t)p[7];
}

static inline uint32_t read_u32_be(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] <<  8) |  (uint32_t)p[3];
}

esp_err_t paud_encode(const paud_header_t *hdr,
                      const int16_t       *samples,
                      uint8_t             *out_buf,
                      size_t               out_size)
{
    if (!hdr || !samples || !out_buf)    return ESP_ERR_INVALID_ARG;
    if (out_size < PAUD_FRAME_BYTES)     return ESP_ERR_INVALID_SIZE;

    uint8_t *p = out_buf;
    memcpy(p, MAGIC, 4);                 p += 4;
    *p++ = PAUD_VERSION;
    *p++ = hdr->type;
    memcpy(p, hdr->stream_id, 16);       p += 16;
    write_u64_be(p, hdr->seq);           p += 8;
    write_u32_be(p, PAUD_PAYLOAD_BYTES); p += 4;
    /* ESP32-P4 is little-endian (RISC-V) — PCM16-LE: direct copy */
    memcpy(p, samples, PAUD_PAYLOAD_BYTES);
    return ESP_OK;
}

esp_err_t paud_decode(const uint8_t    *buf,
                      size_t            len,
                      paud_header_t    *out_hdr,
                      const int16_t   **out_samples)
{
    if (!buf || !out_hdr || !out_samples) return ESP_ERR_INVALID_ARG;

    if (len < PAUD_HEADER_SIZE) {
        ESP_LOGW(TAG, "too short for header: %u bytes", (unsigned)len);
        return ESP_ERR_INVALID_SIZE;
    }

    const uint8_t *p = buf;

    if (memcmp(p, MAGIC, 4) != 0) {
        ESP_LOGW(TAG, "bad magic");
        return ESP_ERR_INVALID_RESPONSE;
    }
    p += 4;

    uint8_t version = *p++;
    if (version != PAUD_VERSION) {
        ESP_LOGW(TAG, "unsupported version %u", version);
        return ESP_ERR_NOT_SUPPORTED;
    }

    out_hdr->type = *p++;
    memcpy(out_hdr->stream_id, p, 16);   p += 16;
    out_hdr->seq           = read_u64_be(p); p += 8;
    out_hdr->payload_bytes = read_u32_be(p); p += 4;

    if (len < PAUD_HEADER_SIZE + out_hdr->payload_bytes) {
        ESP_LOGW(TAG, "truncated payload: have %u need %u",
                 (unsigned)len,
                 (unsigned)(PAUD_HEADER_SIZE + out_hdr->payload_bytes));
        return ESP_ERR_INVALID_SIZE;
    }

    *out_samples = (const int16_t *)p; /* zero-copy into caller's buffer */
    return ESP_OK;
}
