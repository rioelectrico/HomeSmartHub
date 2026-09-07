#include "unity.h"

#include <string.h>

#include "device_config.h"
#include "nvs_flash.h"

/* setUp runs before every test — wipes NVS for isolation */
void setUp(void)
{
    nvs_flash_deinit();
    ESP_ERROR_CHECK(nvs_flash_erase());
    ESP_ERROR_CHECK(nvs_flash_init());
}

void tearDown(void) {}

/* ── helpers ────────────────────────────────────────────────────────── */

#define VALID_ID  "PI-000001"
#define VALID_URL "ws://backend.portero.io/ws/device"

static void fill_secret(char *buf, size_t len, char c)
{
    memset(buf, (int)c, len);
    buf[len] = '\0';
}

/* ── device_id validation ───────────────────────────────────────────── */

static void test_device_id_valid(void)
{
    TEST_ASSERT_EQUAL(ESP_OK, device_config_validate_device_id("PI-000001"));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_validate_device_id("PI-999999"));
}

static void test_device_id_rejects_wrong_prefix(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id("XX-000001"));
}

static void test_device_id_rejects_wrong_length(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id("PI-00001"));   /* 8 */
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id("PI-0000001")); /* 10 */
}

static void test_device_id_rejects_non_digit(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id("PI-00000A"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id("PI-00 001"));
}

static void test_device_id_null_is_rejected(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_config_validate_device_id(NULL));
}

/* ── secret validation ──────────────────────────────────────────────── */

static void test_secret_min_length_passes(void)
{
    char secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE];

    fill_secret(secret, DEVICE_CONFIG_SECRET_MIN_LENGTH, 'a');
    TEST_ASSERT_EQUAL(ESP_OK, device_config_validate_secret(secret));
}

static void test_secret_below_min_is_rejected(void)
{
    char secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE];

    fill_secret(secret, DEVICE_CONFIG_SECRET_MIN_LENGTH - 1U, 'a');
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_config_validate_secret(secret));
}

static void test_secret_max_length_passes(void)
{
    char secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE];

    fill_secret(secret, DEVICE_CONFIG_SECRET_MAX_LENGTH, 'b');
    TEST_ASSERT_EQUAL(ESP_OK, device_config_validate_secret(secret));
}

static void test_secret_above_max_is_rejected(void)
{
    char secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE + 1U];

    fill_secret(secret, DEVICE_CONFIG_SECRET_MAX_LENGTH + 1U, 'b');
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_config_validate_secret(secret));
}

static void test_secret_null_is_rejected(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_config_validate_secret(NULL));
}

/* ── backend_url validation ─────────────────────────────────────────── */

static void test_url_valid_plain(void)
{
    TEST_ASSERT_EQUAL(ESP_OK,
        device_config_validate_backend_url("ws://backend.io/ws/device"));
}

static void test_url_valid_with_port(void)
{
    TEST_ASSERT_EQUAL(ESP_OK,
        device_config_validate_backend_url("ws://backend.io:8080/ws/device"));
}

static void test_url_rejects_wss(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("wss://backend.io/ws/device"));
}

static void test_url_rejects_localhost(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://localhost/ws/device"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://localhost:9000/ws/device"));
}

static void test_url_rejects_127(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://127.0.0.1/ws/device"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://127.1.2.3:9000/ws/device"));
}

static void test_url_rejects_ipv6_loopback(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://[::1]/ws/device"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://[::1]:9000/ws/device"));
}

static void test_url_rejects_credentials(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://user@backend.io/ws/device"));
}

static void test_url_rejects_query(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://backend.io/ws/device?x=1"));
}

static void test_url_rejects_fragment(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://backend.io/ws/device#sec"));
}

static void test_url_rejects_wrong_path(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://backend.io/other"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url("ws://backend.io/ws/device/"));
}

static void test_url_null_is_rejected(void)
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
        device_config_validate_backend_url(NULL));
}

/* ── NVS persistence tests ──────────────────────────────────────────── */

static device_config_t make_config(const char *id, char secret_char,
                                   const char *url)
{
    device_config_t cfg;

    memset(&cfg, 0, sizeof(cfg));
    strncpy(cfg.device_id, id, sizeof(cfg.device_id) - 1U);
    fill_secret(cfg.secret, DEVICE_CONFIG_SECRET_MIN_LENGTH, secret_char);
    strncpy(cfg.backend_url, url, sizeof(cfg.backend_url) - 1U);
    return cfg;
}

static void test_not_provisioned_on_clean_nvs(void)
{
    TEST_ASSERT_FALSE(device_config_is_provisioned());
}

static void test_load_returns_not_found_on_clean_nvs(void)
{
    device_config_t out;

    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, device_config_load(&out));
}

static void test_save_and_load_roundtrip(void)
{
    device_config_t saved = make_config(VALID_ID, 'x', VALID_URL);
    device_config_t loaded;

    memset(&loaded, 0, sizeof(loaded));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&saved));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_load(&loaded));

    TEST_ASSERT_EQUAL_STRING(VALID_ID, loaded.device_id);
    TEST_ASSERT_EQUAL_STRING(VALID_URL, loaded.backend_url);
    TEST_ASSERT_EQUAL_UINT32(DEVICE_CONFIG_SECRET_MIN_LENGTH,
                             strlen(loaded.secret));
}

static void test_is_provisioned_after_save(void)
{
    device_config_t cfg = make_config(VALID_ID, 'y', VALID_URL);

    TEST_ASSERT_FALSE(device_config_is_provisioned());
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg));
    TEST_ASSERT_TRUE(device_config_is_provisioned());
}

static void test_double_save_returns_second_config(void)
{
    device_config_t cfg1 = make_config("PI-000001", 'a', "ws://host1.io/ws/device");
    device_config_t cfg2 = make_config("PI-000002", 'b', "ws://host2.io/ws/device");
    device_config_t loaded;

    memset(&loaded, 0, sizeof(loaded));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg1));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg2));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_load(&loaded));

    TEST_ASSERT_EQUAL_STRING("PI-000002", loaded.device_id);
    TEST_ASSERT_EQUAL_STRING("ws://host2.io/ws/device", loaded.backend_url);
}

static void test_erase_clears_provisioned(void)
{
    device_config_t cfg = make_config(VALID_ID, 'z', VALID_URL);

    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg));
    TEST_ASSERT_TRUE(device_config_is_provisioned());
    TEST_ASSERT_EQUAL(ESP_OK, device_config_erase());
    TEST_ASSERT_FALSE(device_config_is_provisioned());
}

static void test_save_rejects_invalid_config(void)
{
    device_config_t cfg = make_config("BADID", 'a', VALID_URL);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_config_save(&cfg));
    TEST_ASSERT_FALSE(device_config_is_provisioned());
}

/* ── runner ─────────────────────────────────────────────────────────── */

void run_device_config_tests(void)
{
    /* device_id */
    RUN_TEST(test_device_id_valid);
    RUN_TEST(test_device_id_rejects_wrong_prefix);
    RUN_TEST(test_device_id_rejects_wrong_length);
    RUN_TEST(test_device_id_rejects_non_digit);
    RUN_TEST(test_device_id_null_is_rejected);
    /* secret */
    RUN_TEST(test_secret_min_length_passes);
    RUN_TEST(test_secret_below_min_is_rejected);
    RUN_TEST(test_secret_max_length_passes);
    RUN_TEST(test_secret_above_max_is_rejected);
    RUN_TEST(test_secret_null_is_rejected);
    /* backend_url */
    RUN_TEST(test_url_valid_plain);
    RUN_TEST(test_url_valid_with_port);
    RUN_TEST(test_url_rejects_wss);
    RUN_TEST(test_url_rejects_localhost);
    RUN_TEST(test_url_rejects_127);
    RUN_TEST(test_url_rejects_ipv6_loopback);
    RUN_TEST(test_url_rejects_credentials);
    RUN_TEST(test_url_rejects_query);
    RUN_TEST(test_url_rejects_fragment);
    RUN_TEST(test_url_rejects_wrong_path);
    RUN_TEST(test_url_null_is_rejected);
    /* NVS */
    RUN_TEST(test_not_provisioned_on_clean_nvs);
    RUN_TEST(test_load_returns_not_found_on_clean_nvs);
    RUN_TEST(test_save_and_load_roundtrip);
    RUN_TEST(test_is_provisioned_after_save);
    RUN_TEST(test_double_save_returns_second_config);
    RUN_TEST(test_erase_clears_provisioned);
    RUN_TEST(test_save_rejects_invalid_config);
}
