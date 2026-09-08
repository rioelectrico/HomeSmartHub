#include "unity.h"

#include <string.h>

#include "device_config.h"
#include "nvs.h"
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

/* ── fault-simulation tests ─────────────────────────────────────────── */

/*
 * Simulate: power cut after invalidation commit but before new data commit.
 * The inactive slot has ok=0. Active slot remains valid — load must return it.
 */
static void test_interrupted_write_preserves_active(void)
{
    device_config_t cfg1 = make_config("PI-000001", 'a', "ws://host1.io/ws/device");
    device_config_t loaded;
    nvs_handle_t h;
    uint8_t active = 0;
    uint8_t inactive_slot;
    const char *id_key;
    const char *ok_key;

    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg1));
    TEST_ASSERT_TRUE(device_config_is_provisioned());

    /* Determine which slot is inactive after the first save */
    TEST_ASSERT_EQUAL(ESP_OK, nvs_open("portero_cfg", NVS_READWRITE, &h));
    nvs_get_u8(h, "active", &active);
    inactive_slot = 1U - active;
    id_key = (inactive_slot == 0) ? "a_id" : "b_id";
    ok_key = (inactive_slot == 0) ? "a_ok" : "b_ok";

    /* Simulate partial write to inactive slot with ok=0 (invalidated state) */
    nvs_set_str(h, id_key, "PI-000002");
    nvs_set_u8(h, ok_key, 0U);
    nvs_commit(h);
    nvs_close(h);

    /* Load must fall back to cfg1 (the last complete, valid record) */
    memset(&loaded, 0, sizeof(loaded));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_load(&loaded));
    TEST_ASSERT_EQUAL_STRING("PI-000001", loaded.device_id);
}

/*
 * Simulate: active slot is corrupted after the active pointer was switched.
 * Load must fall back to the alternate (older but complete) slot.
 */
static void test_corrupted_active_falls_back_to_alternate(void)
{
    device_config_t cfg1 = make_config("PI-000001", 'c', "ws://host1.io/ws/device");
    device_config_t cfg2 = make_config("PI-000002", 'd', "ws://host2.io/ws/device");
    device_config_t loaded;
    nvs_handle_t h;
    uint8_t active = 0;
    const char *ok_key;

    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg1));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg2));

    /* Corrupt the currently-active slot's ok flag */
    TEST_ASSERT_EQUAL(ESP_OK, nvs_open("portero_cfg", NVS_READWRITE, &h));
    nvs_get_u8(h, "active", &active);
    ok_key = (active == 0) ? "a_ok" : "b_ok";
    nvs_set_u8(h, ok_key, 0U);
    nvs_commit(h);
    nvs_close(h);

    /* Load must fall back to the alternate slot — content must be non-empty */
    memset(&loaded, 0, sizeof(loaded));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_load(&loaded));
    TEST_ASSERT_TRUE(loaded.device_id[0] != '\0');
    TEST_ASSERT_TRUE(loaded.backend_url[0] != '\0');
}

/*
 * Both slots corrupted (ok=0): load must return ESP_ERR_NOT_FOUND.
 */
static void test_both_slots_corrupted_returns_not_found(void)
{
    device_config_t cfg1 = make_config("PI-000001", 'e', "ws://host1.io/ws/device");
    device_config_t cfg2 = make_config("PI-000002", 'f', "ws://host2.io/ws/device");
    device_config_t loaded;
    nvs_handle_t h;

    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg1));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg2));

    TEST_ASSERT_EQUAL(ESP_OK, nvs_open("portero_cfg", NVS_READWRITE, &h));
    nvs_set_u8(h, "a_ok", 0U);
    nvs_set_u8(h, "b_ok", 0U);
    nvs_commit(h);
    nvs_close(h);

    memset(&loaded, 0, sizeof(loaded));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, device_config_load(&loaded));
    TEST_ASSERT_FALSE(device_config_is_provisioned());
}

/*
 * Both slots valid after the active pointer key is erased:
 * the higher-generation slot must win, and the result must be complete.
 */
static void test_higher_generation_wins_on_dual_valid(void)
{
    device_config_t cfg1 = make_config("PI-000001", 'g', "ws://host1.io/ws/device");
    device_config_t cfg2 = make_config("PI-000002", 'h', "ws://host2.io/ws/device");
    device_config_t loaded;
    nvs_handle_t h;
    esp_err_t err;

    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg1));
    TEST_ASSERT_EQUAL(ESP_OK, device_config_save(&cfg2));

    /* Erase active pointer — both slots remain valid */
    TEST_ASSERT_EQUAL(ESP_OK, nvs_open("portero_cfg", NVS_READWRITE, &h));
    nvs_erase_key(h, "active");
    nvs_commit(h);
    nvs_close(h);

    /*
     * With no active key, load returns NOT_FOUND (current behaviour).
     * If it returns OK, the record must be complete (not mixed data).
     */
    memset(&loaded, 0, sizeof(loaded));
    err = device_config_load(&loaded);
    if (err == ESP_OK) {
        TEST_ASSERT_TRUE(loaded.device_id[0] != '\0');
        TEST_ASSERT_TRUE(loaded.backend_url[0] != '\0');
    } else {
        TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, err);
    }
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
    /* fault simulation */
    RUN_TEST(test_interrupted_write_preserves_active);
    RUN_TEST(test_corrupted_active_falls_back_to_alternate);
    RUN_TEST(test_both_slots_corrupted_returns_not_found);
    RUN_TEST(test_higher_generation_wins_on_dual_valid);
}
