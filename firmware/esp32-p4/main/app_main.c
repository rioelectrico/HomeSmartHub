#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

#include "driver/gpio.h"
#include "esp_chip_info.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "board_audio.h"
#include "board_port.h"
#include "device_auth.h"
#include "device_config.h"
#include "network_manager.h"
#include "provisioning_web.h"
#include "ws_transport.h"

#define FACTORY_RESET_GPIO    GPIO_NUM_0
#define FACTORY_RESET_HOLD_MS 3000

static const char *TAG = "portero";

/* Background task: polls BOOT button while device is running.
 * Hold for 3 s → erase NVS → restart into provisioning mode. */
static void factory_reset_task(void *arg)
{
    (void)arg;
    for (;;) {
        if (gpio_get_level(FACTORY_RESET_GPIO) == 0) {
            ESP_LOGW(TAG, "BOOT held — keep holding 3 s for factory reset");
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
                ESP_LOGW(TAG, "Factory reset: erasing NVS and restarting");
                provisioning_web_stop();
                ws_transport_stop();
                nvs_flash_erase();
                esp_restart();
            } else {
                ESP_LOGI(TAG, "Factory reset cancelled");
                while (gpio_get_level(FACTORY_RESET_GPIO) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(50));
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

static void on_ws_event(const ws_transport_event_t *ev, void *ctx);

/* Launched as a one-shot task so ws_transport_start runs with its own stack,
 * not on the event-loop task which has only ~2 KB. */
static void ws_start_task(void *arg)
{
    device_config_t *cfg = (device_config_t *)arg;
    ws_transport_start(cfg->backend_url, cfg->device_id, cfg->secret,
                       on_ws_event, NULL);
    device_auth_zeroize(cfg->secret, sizeof(cfg->secret));
    free(cfg);
    vTaskDelete(NULL);
}

static void on_ws_event(const ws_transport_event_t *ev, void *ctx)
{
    (void)ctx;
    switch (ev->type) {
    case WS_TRANSPORT_EVENT_ONLINE:
        ESP_LOGI(TAG, "Device ONLINE (heartbeat every %" PRIu32 " s)",
                 ev->online.heartbeat_interval_s);
        break;
    case WS_TRANSPORT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "WebSocket disconnected — reconnecting");
        break;
    case WS_TRANSPORT_EVENT_COMMAND:
        ESP_LOGI(TAG, "Command received: %d id=%s",
                 (int)ev->command.command, ev->command.command_id);
        switch (ev->command.command) {
        case PORTERO_COMMAND_DEVICE_STATUS_REQUEST:
            ws_transport_send_command_result(ev->command.command_id,
                                             PORTERO_RESULT_COMPLETED,
                                             PORTERO_ERROR_CODE_NONE,
                                             "{}");
            break;
        case PORTERO_COMMAND_CAMERA_CAPTURE:
            ws_transport_send_command_result(ev->command.command_id,
                                             PORTERO_RESULT_FAILED,
                                             PORTERO_ERROR_CODE_CAMERA_NOT_READY,
                                             "{}");
            break;
        default:
            ws_transport_send_command_result(ev->command.command_id,
                                             PORTERO_RESULT_FAILED,
                                             PORTERO_ERROR_CODE_INVALID_COMMAND,
                                             "{}");
            break;
        }
        break;
    }
}

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
        ws_transport_stop();
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
            device_config_t *cfg = malloc(sizeof(device_config_t));
            if (cfg && device_config_load(cfg) == ESP_OK) {
                ESP_LOGI(TAG, "Device provisioned — connecting to backend");
                xTaskCreate(ws_start_task, "ws_start", 6144, cfg, 5, NULL);
            } else {
                ESP_LOGE(TAG, "Failed to load device config");
                free(cfg);
            }
        }
        break;

    case NETWORK_EVENT_LOST_IP:
        ESP_LOGI(TAG, "[ETH] Lost IP");
        provisioning_web_stop();
        ws_transport_stop();
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

    gpio_config_t io = {
        .pin_bit_mask = (1ULL << FACTORY_RESET_GPIO),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    xTaskCreate(factory_reset_task, "factory_reset", 2048, NULL, 3, NULL);

    esp_netif_init();
    esp_event_loop_create_default();

    esp_eth_handle_t eth_handle = NULL;
    esp_netif_t     *eth_netif  = NULL;

    ret = board_port_init(&eth_handle, &eth_netif);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "board_port_init failed: %s", esp_err_to_name(ret));
        return;
    }

    /* FW2-3: physical ES8311 init verification — remove after confirmed */
    ret = board_audio_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "board_audio_init failed: %s", esp_err_to_name(ret));
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
