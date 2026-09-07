#include "network_manager.h"
#include "esp_event.h"
#include "esp_eth.h"
#include "esp_netif_types.h"
#include "esp_log.h"
#include <string.h>

static const char *TAG = "net_mgr";

typedef struct {
    network_event_callback_t cb;
    void *cb_ctx;
    bool has_ip;
    esp_event_handler_instance_t eth_instance;
    esp_event_handler_instance_t ip_instance;
} nm_state_t;

static nm_state_t s_nm;
static bool s_started = false;

static void on_eth_event(void *arg, esp_event_base_t base,
                          int32_t event_id, void *event_data)
{
    (void)base;
    (void)event_data; /* eth handle ignored — single interface */
    nm_state_t *nm = (nm_state_t *)arg;

    if (event_id == ETHERNET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "Link up");
        nm->cb(NETWORK_EVENT_LINK_UP, NULL, nm->cb_ctx);
    } else if (event_id == ETHERNET_EVENT_DISCONNECTED) {
        ESP_LOGI(TAG, "Link down");
        nm->has_ip = false;
        nm->cb(NETWORK_EVENT_LINK_DOWN, NULL, nm->cb_ctx);
    }
}

static void on_ip_event(void *arg, esp_event_base_t base,
                         int32_t event_id, void *event_data)
{
    (void)base;
    nm_state_t *nm = (nm_state_t *)arg;

    if (event_id == IP_EVENT_ETH_GOT_IP) {
        if (!nm->has_ip) {
            nm->has_ip = true;
            ip_event_got_ip_t *ev = (ip_event_got_ip_t *)event_data;
            const esp_netif_ip_info_t *ip = ev ? &ev->ip_info : NULL;
            if (ip) {
                ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&ip->ip));
            } else {
                ESP_LOGI(TAG, "Got IP (no info)");
            }
            nm->cb(NETWORK_EVENT_GOT_IP, ip, nm->cb_ctx);
        }
        /* duplicate GOT_IP while has_ip is already true → silently ignored */
    } else if (event_id == IP_EVENT_ETH_LOST_IP) {
        if (nm->has_ip) {
            nm->has_ip = false;
            ESP_LOGI(TAG, "Lost IP");
            nm->cb(NETWORK_EVENT_LOST_IP, NULL, nm->cb_ctx);
        }
    }
}

esp_err_t network_manager_start(network_event_callback_t callback, void *ctx)
{
    if (!callback) return ESP_ERR_INVALID_ARG;
    if (s_started)  return ESP_ERR_INVALID_STATE;

    memset(&s_nm, 0, sizeof(s_nm));
    s_nm.cb     = callback;
    s_nm.cb_ctx = ctx;

    esp_err_t ret;
    ret = esp_event_handler_instance_register(ETH_EVENT, ESP_EVENT_ANY_ID,
                                              on_eth_event, &s_nm,
                                              &s_nm.eth_instance);
    if (ret != ESP_OK) return ret;

    ret = esp_event_handler_instance_register(IP_EVENT, ESP_EVENT_ANY_ID,
                                              on_ip_event, &s_nm,
                                              &s_nm.ip_instance);
    if (ret != ESP_OK) {
        esp_event_handler_instance_unregister(ETH_EVENT, ESP_EVENT_ANY_ID,
                                              s_nm.eth_instance);
        return ret;
    }

    s_started = true;
    return ESP_OK;
}

esp_err_t network_manager_stop(void)
{
    if (!s_started) return ESP_OK;

    esp_event_handler_instance_unregister(ETH_EVENT, ESP_EVENT_ANY_ID,
                                          s_nm.eth_instance);
    esp_event_handler_instance_unregister(IP_EVENT, ESP_EVENT_ANY_ID,
                                          s_nm.ip_instance);
    memset(&s_nm, 0, sizeof(s_nm));
    s_started = false;
    return ESP_OK;
}

bool network_manager_has_ip(void)
{
    return s_started && s_nm.has_ip;
}
