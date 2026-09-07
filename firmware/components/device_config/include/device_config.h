#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE    10U   /* "PI-XXXXXX\0" */
#define DEVICE_CONFIG_SECRET_MIN_LENGTH        32U
#define DEVICE_CONFIG_SECRET_MAX_LENGTH        128U
#define DEVICE_CONFIG_SECRET_BUFFER_SIZE       129U
#define DEVICE_CONFIG_BACKEND_URL_MAX_LENGTH   256U
#define DEVICE_CONFIG_BACKEND_URL_BUFFER_SIZE  257U

typedef struct {
    char device_id[DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE];
    char secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE];
    char backend_url[DEVICE_CONFIG_BACKEND_URL_BUFFER_SIZE];
} device_config_t;

/** Validates device_id matches ^PI-[0-9]{6}$. */
esp_err_t device_config_validate_device_id(const char *device_id);

/** Validates secret length is in [32, 128]. */
esp_err_t device_config_validate_secret(const char *secret);

/**
 * Validates backend_url is ws://host[:port]/ws/device with no credentials,
 * query, fragment, and host is not localhost/127.x/::1.
 */
esp_err_t device_config_validate_backend_url(const char *backend_url);

/** Returns true if NVS holds at least one complete, valid configuration. */
bool device_config_is_provisioned(void);

/**
 * Loads the active configuration from NVS into out.
 * Returns ESP_ERR_NOT_FOUND if not provisioned.
 */
esp_err_t device_config_load(device_config_t *out);

/**
 * Validates and saves config to NVS using a two-slot atomic write.
 * Protects against data corruption on power loss during write.
 */
esp_err_t device_config_save(const device_config_t *config);

/** Erases all configuration from NVS. */
esp_err_t device_config_erase(void);

#ifdef __cplusplus
}
#endif
