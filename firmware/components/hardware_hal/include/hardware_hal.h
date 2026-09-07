#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool camera_available;
    bool audio_available;
    bool ethernet_available;
} hardware_hal_capabilities_t;

/** Reports only capabilities selected by a concrete board implementation. */
void hardware_hal_get_capabilities(hardware_hal_capabilities_t *capabilities);

/** Board-neutral stubs: all return ESP_ERR_NOT_SUPPORTED. */
esp_err_t hardware_hal_camera_start(void);
esp_err_t hardware_hal_audio_start(void);
esp_err_t hardware_hal_ethernet_start(void);

#ifdef __cplusplus
}
#endif
