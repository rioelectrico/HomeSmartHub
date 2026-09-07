#include "unity.h"

#include <stdint.h>
#include <string.h>

#include "device_auth.h"

static void test_generate_boot_id_returns_32_lowercase_hex_chars(void)
{
    char boot_id[DEVICE_AUTH_BOOT_ID_BUFFER_SIZE];
    size_t i;

    TEST_ASSERT_EQUAL(ESP_OK, device_auth_generate_boot_id(boot_id));
    TEST_ASSERT_EQUAL_UINT32(DEVICE_AUTH_BOOT_ID_LENGTH, strlen(boot_id));

    for (i = 0U; i < DEVICE_AUTH_BOOT_ID_LENGTH; i++) {
        char c = boot_id[i];
        TEST_ASSERT_TRUE((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    }
}

static void test_generate_boot_id_null_is_rejected(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_auth_generate_boot_id(NULL));
}

static void test_two_boot_ids_differ(void)
{
    char id1[DEVICE_AUTH_BOOT_ID_BUFFER_SIZE];
    char id2[DEVICE_AUTH_BOOT_ID_BUFFER_SIZE];

    TEST_ASSERT_EQUAL(ESP_OK, device_auth_generate_boot_id(id1));
    TEST_ASSERT_EQUAL(ESP_OK, device_auth_generate_boot_id(id2));
    TEST_ASSERT_NOT_EQUAL(0, memcmp(id1, id2, DEVICE_AUTH_BOOT_ID_LENGTH));
}

static void test_hmac_known_vector(void)
{
    char hmac[DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE];

    TEST_ASSERT_EQUAL(ESP_OK,
        device_auth_hmac_hex("secret", "PI-000001", "boot-123", "nonce-456",
                             hmac));
    TEST_ASSERT_EQUAL_STRING(
        "ad78e27952573ad8aa6ea6d2593f5db5ee69fe1102aaaf6aca4016dc43210975",
        hmac);
}

static void test_hmac_output_is_64_lowercase_hex_chars(void)
{
    char hmac[DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE];
    size_t i;

    TEST_ASSERT_EQUAL(ESP_OK,
        device_auth_hmac_hex("k", "d", "b", "n", hmac));
    TEST_ASSERT_EQUAL_UINT32(DEVICE_AUTH_HMAC_HEX_LENGTH, strlen(hmac));

    for (i = 0U; i < DEVICE_AUTH_HMAC_HEX_LENGTH; i++) {
        char c = hmac[i];
        TEST_ASSERT_TRUE((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
    }
}

static void test_hmac_null_inputs_are_rejected(void)
{
    char hmac[DEVICE_AUTH_HMAC_HEX_BUFFER_SIZE];

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_auth_hmac_hex(NULL, "d", "b", "n", hmac));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_auth_hmac_hex("k", NULL, "b", "n", hmac));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_auth_hmac_hex("k", "d", NULL, "n", hmac));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_auth_hmac_hex("k", "d", "b", NULL, hmac));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_auth_hmac_hex("k", "d", "b", "n", NULL));
}

static void test_zeroize_clears_buffer(void)
{
    uint8_t buf[16];
    size_t i;

    memset(buf, 0xAA, sizeof(buf));
    device_auth_zeroize(buf, sizeof(buf));

    for (i = 0U; i < sizeof(buf); i++) {
        TEST_ASSERT_EQUAL_UINT8(0U, buf[i]);
    }
}

void run_device_auth_tests(void)
{
    RUN_TEST(test_generate_boot_id_returns_32_lowercase_hex_chars);
    RUN_TEST(test_generate_boot_id_null_is_rejected);
    RUN_TEST(test_two_boot_ids_differ);
    RUN_TEST(test_hmac_known_vector);
    RUN_TEST(test_hmac_output_is_64_lowercase_hex_chars);
    RUN_TEST(test_hmac_null_inputs_are_rejected);
    RUN_TEST(test_zeroize_clears_buffer);
}
