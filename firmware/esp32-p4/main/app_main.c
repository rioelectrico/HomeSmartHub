#include <inttypes.h>
#include <stdio.h>

#include "esp_chip_info.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"

#include "board_port.h"
#include "network_manager.h"

static const char *TAG = "portero";

static void on_network_event(network_event_type_t event,
                              const esp_netif_ip_info_t *ip,
                              void *ctx)
{
    (void)ctx;
    switch (event) {
    case NETWORK_EVENT_LINK_UP:
        ESP_LOGI(TAG, "[ETH] Link up");
        break;
    case NETWORK_EVENT_LINK_DOWN:
        ESP_LOGI(TAG, "[ETH] Link down");
        break;
    case NETWORK_EVENT_GOT_IP:
        if (ip) {
            ESP_LOGI(TAG, "[ETH] IP: " IPSTR "  GW: " IPSTR,
                     IP2STR(&ip->ip), IP2STR(&ip->gw));
        }
        break;
    case NETWORK_EVENT_LOST_IP:
        ESP_LOGI(TAG, "[ETH] Lost IP");
        break;
    }
}

void app_main(void)
{
    esp_err_t ret;

    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    esp_netif_init();
    esp_event_loop_create_default();

    esp_eth_handle_t eth_handle = NULL;
    esp_netif_t     *eth_netif  = NULL;

    ret = board_port_init(&eth_handle, &eth_netif);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "board_port_init failed: %s", esp_err_to_name(ret));
        return;
    }

    ret = network_manager_start(on_network_event, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "network_manager_start failed: %s", esp_err_to_name(ret));
        return;
    }

    ret = esp_eth_start(eth_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_eth_start failed: %s", esp_err_to_name(ret));
        return;
    }

    esp_chip_info_t chip_info;
    esp_chip_info(&chip_info);
    ESP_LOGI(TAG, "Portero FW-1 Ethernet started (model=%d rev=%d)",
             chip_info.model, chip_info.revision);
}
