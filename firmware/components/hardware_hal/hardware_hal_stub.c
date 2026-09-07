#include "hardware_hal.h"

#include <stddef.h>

void hardware_hal_get_capabilities(hardware_hal_capabilities_t *capabilities)
{
    if (capabilities == NULL) {
        return;
    }

    capabilities->camera_available = false;
    capabilities->audio_available = false;
    capabilities->ethernet_available = false;
}

esp_err_t hardware_hal_camera_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t hardware_hal_audio_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t hardware_hal_ethernet_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}
