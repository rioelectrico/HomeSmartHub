#include <string.h>
#include "unity.h"
#include "network_manager.h"
#include "esp_event.h"
#include "esp_eth.h"
#include "esp_netif.h"
#include "esp_netif_types.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"

static const char *TAG = "test_nm";

#define WAIT_TICKS  pdMS_TO_TICKS(500)
#define NOEVENT_MS  150

/* ── test context ────────────────────────────────────────────────────── */

typedef struct {
    network_event_type_t events[8];
    int count;
    SemaphoreHandle_t sem;
} nm_test_ctx_t;

static nm_test_ctx_t s_ctx;

static void nm_test_cb(network_event_type_t event,
                        const esp_netif_ip_info_t *ip,
                        void *ctx)
{
    nm_test_ctx_t *t = (nm_test_ctx_t *)ctx;
    if (t->count < 8) {
        t->events[t->count++] = event;
    }
    xSemaphoreGive(t->sem);
}

static void setup(void)
{
    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.sem = xSemaphoreCreateBinary();
    TEST_ASSERT_NOT_NULL(s_ctx.sem);
    TEST_ESP_OK(network_manager_start(nm_test_cb, &s_ctx));
}

static void teardown(void)
{
    network_manager_stop();
    vSemaphoreDelete(s_ctx.sem);
    s_ctx.sem = NULL;
}

static bool wait_event(void)
{
    return xSemaphoreTake(s_ctx.sem, WAIT_TICKS) == pdTRUE;
}

/* ── event helpers ───────────────────────────────────────────────────── */

static void post_eth_connected(void)
{
    TEST_ESP_OK(esp_event_post(ETH_EVENT, ETHERNET_EVENT_CONNECTED,
                               NULL, 0, portMAX_DELAY));
}

static void post_eth_disconnected(void)
{
    TEST_ESP_OK(esp_event_post(ETH_EVENT, ETHERNET_EVENT_DISCONNECTED,
                               NULL, 0, portMAX_DELAY));
}

static void post_got_ip(void)
{
    ip_event_got_ip_t evt = {0};
    TEST_ESP_OK(esp_event_post(IP_EVENT, IP_EVENT_ETH_GOT_IP,
                               &evt, sizeof(evt), portMAX_DELAY));
}

static void post_lost_ip(void)
{
    TEST_ESP_OK(esp_event_post(IP_EVENT, IP_EVENT_ETH_LOST_IP,
                               NULL, 0, portMAX_DELAY));
}

/* ── tests ───────────────────────────────────────────────────────────── */

static void test_start_stop_lifecycle(void)
{
    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.sem = xSemaphoreCreateBinary();
    TEST_ASSERT_NOT_NULL(s_ctx.sem);

    TEST_ESP_OK(network_manager_start(nm_test_cb, &s_ctx));
    TEST_ASSERT_FALSE(network_manager_has_ip());
    TEST_ESP_OK(network_manager_stop());
    /* stop without start must not crash */
    TEST_ESP_OK(network_manager_stop());

    vSemaphoreDelete(s_ctx.sem);
}

static void test_double_start_rejected(void)
{
    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.sem = xSemaphoreCreateBinary();
    TEST_ASSERT_NOT_NULL(s_ctx.sem);

    TEST_ESP_OK(network_manager_start(nm_test_cb, &s_ctx));
    esp_err_t rc = network_manager_start(nm_test_cb, &s_ctx);
    TEST_ASSERT_NOT_EQUAL(ESP_OK, rc);
    network_manager_stop();
    vSemaphoreDelete(s_ctx.sem);
}

static void test_link_up_alone_not_ready(void)
{
    setup();
    post_eth_connected();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout waiting for LINK_UP");

    TEST_ASSERT_EQUAL_INT(1, s_ctx.count);
    TEST_ASSERT_EQUAL_INT(NETWORK_EVENT_LINK_UP, s_ctx.events[0]);
    TEST_ASSERT_FALSE(network_manager_has_ip());
    teardown();
}

static void test_got_ip_emits_ready_and_sets_has_ip(void)
{
    setup();
    post_eth_connected();
    TEST_ASSERT_TRUE(wait_event());             /* LINK_UP */
    post_got_ip();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout waiting for GOT_IP");

    TEST_ASSERT_EQUAL_INT(2, s_ctx.count);
    TEST_ASSERT_EQUAL_INT(NETWORK_EVENT_LINK_UP,  s_ctx.events[0]);
    TEST_ASSERT_EQUAL_INT(NETWORK_EVENT_GOT_IP,   s_ctx.events[1]);
    TEST_ASSERT_TRUE(network_manager_has_ip());
    teardown();
}

static void test_duplicate_got_ip_idempotent(void)
{
    setup();
    post_got_ip();
    TEST_ASSERT_TRUE(wait_event());             /* first GOT_IP */

    post_got_ip();                              /* duplicate — must be silent */
    vTaskDelay(pdMS_TO_TICKS(NOEVENT_MS));

    TEST_ASSERT_EQUAL_INT_MESSAGE(1, s_ctx.count, "duplicate GOT_IP fired callback");
    TEST_ASSERT_TRUE(network_manager_has_ip());
    teardown();
}

static void test_link_down_clears_has_ip(void)
{
    setup();
    post_got_ip();
    TEST_ASSERT_TRUE(wait_event());             /* GOT_IP */
    TEST_ASSERT_TRUE(network_manager_has_ip());

    post_eth_disconnected();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout waiting for LINK_DOWN");

    TEST_ASSERT_EQUAL_INT(NETWORK_EVENT_LINK_DOWN, s_ctx.events[s_ctx.count - 1]);
    TEST_ASSERT_FALSE(network_manager_has_ip());
    teardown();
}

static void test_lost_ip_clears_has_ip(void)
{
    setup();
    post_got_ip();
    TEST_ASSERT_TRUE(wait_event());             /* GOT_IP */
    TEST_ASSERT_TRUE(network_manager_has_ip());

    post_lost_ip();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout waiting for LOST_IP");

    TEST_ASSERT_EQUAL_INT(NETWORK_EVENT_LOST_IP, s_ctx.events[s_ctx.count - 1]);
    TEST_ASSERT_FALSE(network_manager_has_ip());
    teardown();
}

static void test_link_down_without_prior_ip_does_not_crash(void)
{
    setup();
    post_eth_connected();
    TEST_ASSERT_TRUE(wait_event());             /* LINK_UP */
    post_eth_disconnected();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout after LINK_DOWN");
    TEST_ASSERT_FALSE(network_manager_has_ip());
    teardown();
}

static void test_reconnect_sequence(void)
{
    /* link-up → got-ip → link-down → link-up → got-ip again */
    setup();
    post_eth_connected();
    TEST_ASSERT_TRUE(wait_event());
    post_got_ip();
    TEST_ASSERT_TRUE(wait_event());
    TEST_ASSERT_TRUE(network_manager_has_ip());

    post_eth_disconnected();
    TEST_ASSERT_TRUE(wait_event());
    TEST_ASSERT_FALSE(network_manager_has_ip());

    post_eth_connected();
    TEST_ASSERT_TRUE(wait_event());
    post_got_ip();
    TEST_ASSERT_TRUE_MESSAGE(wait_event(), "timeout on reconnect GOT_IP");
    TEST_ASSERT_TRUE(network_manager_has_ip());
    teardown();
}

/* ── runner ──────────────────────────────────────────────────────────── */

void run_network_events_tests(void)
{
    /* The default event loop must exist for all network tests.
       ESP_ERR_INVALID_STATE is returned if it was already created. */
    esp_err_t rc = esp_event_loop_create_default();
    if (rc != ESP_OK && rc != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "Failed to create event loop: %s", esp_err_to_name(rc));
        return;
    }

    RUN_TEST(test_start_stop_lifecycle);
    RUN_TEST(test_double_start_rejected);
    RUN_TEST(test_link_up_alone_not_ready);
    RUN_TEST(test_got_ip_emits_ready_and_sets_has_ip);
    RUN_TEST(test_duplicate_got_ip_idempotent);
    RUN_TEST(test_link_down_clears_has_ip);
    RUN_TEST(test_lost_ip_clears_has_ip);
    RUN_TEST(test_link_down_without_prior_ip_does_not_crash);
    RUN_TEST(test_reconnect_sequence);
}
