#include <inttypes.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "esp_chip_info.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "board_port.h"
#include "device_config.h"
#include "network_manager.h"
#include "provisioning_web.h"

#define FACTORY_RESET_GPIO   GPIO_NUM_0
#define FACTORY_RESET_HOLD_MS 3000

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
        provisioning_web_stop();
        break;
    case NETWORK_EVENT_GOT_IP:
        if (ip) {
            ESP_LOGI(TAG, "[ETH] IP: " IPSTR "  GW: " IPSTR,
                     IP2STR(&ip->ip), IP2STR(&ip->gw));
        }
        if (!device_config_is_provisioned()) {
            ESP_LOGI(TAG, "Device not provisioned — starting web portal");
            provisioning_web_start();
        } else {
            ESP_LOGI(TAG, "Device provisioned — ready for WebSocket (Task 7)");
        }
        break;
    case NETWORK_EVENT_LOST_IP:
        ESP_LOGI(TAG, "[ETH] Lost IP");
        provisioning_web_stop();
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

    /* Factory reset: hold BOOT button (GPIO 0) for 3 s at startup */
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << FACTORY_RESET_GPIO),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    if (gpio_get_level(FACTORY_RESET_GPIO) == 0) {
        ESP_LOGW(TAG, "BOOT held — release within 3 s to cancel factory reset");
        int elapsed = 0;
        bool cancelled = false;
        while (elapsed < FACTORY_RESET_HOLD_MS) {
            vTaskDelay(pdMS_TO_TICKS(100));
            elapsed += 100;
            if (gpio_get_level(FACTORY_RESET_GPIO) != 0) {
                cancelled = true;
                break;
            }
        }
        if (!cancelled) {
            ESP_LOGW(TAG, "Factory reset: erasing NVS");
            nvs_flash_erase();
            nvs_flash_init();
            ESP_LOGI(TAG, "Factory reset complete — device will enter provisioning mode");
        } else {
            ESP_LOGI(TAG, "Factory reset cancelled");
        }
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
    ESP_LOGI(TAG, "Portero FW-1 started (model=%d rev=%d)",
             chip_info.model, chip_info.revision);
}
