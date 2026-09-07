#include "device_auth.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_random.h"
#include "mbedtls/md.h"
#include "mbedtls/platform_util.h"

#define CANONICAL_BUFFER_SIZE 512U

static void bytes_to_hex(const uint8_t *bytes, size_t len, char *hex_out)
{
    static const char hex_chars[] = "0123456789abcdef";
    size_t i;

    for (i = 0U; i < len; i++) {
        hex_out[i * 2U]      = hex_chars[bytes[i] >> 4U];
        hex_out[i * 2U + 1U] = hex_chars[bytes[i] & 0x0fU];
    }
}

esp_err_t device_auth_generate_boot_id(char *boot_id_out)
{
    uint8_t random_bytes[16];

    if (boot_id_out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_fill_random(random_bytes, sizeof(random_bytes));
    bytes_to_hex(random_bytes, sizeof(random_bytes), boot_id_out);
    boot_id_out[DEVICE_AUTH_BOOT_ID_LENGTH] = '\0';

    mbedtls_platform_zeroize(random_bytes, sizeof(random_bytes));
    return ESP_OK;
}

esp_err_t device_auth_hmac_hex(const char *secret,
                               const char *device_id,
                               const char *boot_id,
                               const char *nonce,
                               char *hmac_hex_out)
{
    mbedtls_md_context_t ctx;
    uint8_t digest[32];
    char canonical[CANONICAL_BUFFER_SIZE];
    int written;
    esp_err_t ret = ESP_OK;

    if (secret == NULL || device_id == NULL || boot_id == NULL ||
        nonce == NULL || hmac_hex_out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    written = snprintf(canonical, sizeof(canonical),
                       "v1\n%s\n%s\n%s", device_id, boot_id, nonce);
    if (written < 0 || (size_t)written >= sizeof(canonical)) {
        return ESP_ERR_INVALID_ARG;
    }

    mbedtls_md_init(&ctx);

    if (mbedtls_md_setup(&ctx,
                         mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),
                         1) != 0) {
        ret = ESP_FAIL;
        goto cleanup;
    }

    if (mbedtls_md_hmac_starts(&ctx,
                               (const uint8_t *)secret,
                               strlen(secret)) != 0 ||
        mbedtls_md_hmac_update(&ctx,
                               (const uint8_t *)canonical,
                               (size_t)written) != 0 ||
        mbedtls_md_hmac_finish(&ctx, digest) != 0) {
        ret = ESP_FAIL;
        goto cleanup;
    }

    bytes_to_hex(digest, sizeof(digest), hmac_hex_out);
    hmac_hex_out[DEVICE_AUTH_HMAC_HEX_LENGTH] = '\0';

cleanup:
    mbedtls_md_free(&ctx);
    mbedtls_platform_zeroize(digest, sizeof(digest));
    mbedtls_platform_zeroize(canonical, sizeof(canonical));
    return ret;
}

void device_auth_zeroize(void *buf, size_t len)
{
    mbedtls_platform_zeroize(buf, len);
}
