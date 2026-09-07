#pragma once

#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DEVICE_AUTH_BOOT_ID_LENGTH       32U
#define DEVICE_AUTH_BOOT_ID_BUFFER_SIZE  (DEVICE_AUTH_BOOT_ID_LENGTH + 1U)
#define DEVICE_AUTH_HMAC_HEX_LENGTH      64U
#define DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE (DEVICE_AUTH_HMAC_HEX_LENGTH + 1U)

/**
 * Fills boot_id_out with 32 lowercase hex chars derived from 128 bits of
 * hardware RNG. boot_id_out must be at least DEVICE_AUTH_BOOT_ID_BUFFER_SIZE.
 * Generate once per boot; the result survives reconnections.
 */
esp_err_t device_auth_generate_boot_id(char *boot_id_out);

/**
 * Computes HMAC-SHA256(secret, "v1\n<device_id>\n<boot_id>\n<nonce>") and
 * writes 64 lowercase hex chars into hmac_hex_out.
 * hmac_hex_out must be at least DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE.
 * All intermediate sensitive buffers are zeroized before return.
 */
esp_err_t device_auth_hmac_hex(const char *secret,
                               const char *device_id,
                               const char *boot_id,
                               const char *nonce,
                               char *hmac_hex_out);

/** Zeroizes len bytes at buf using a memory barrier to prevent optimization. */
void device_auth_zeroize(void *buf, size_t len);

#ifdef __cplusplus
}
#endif
