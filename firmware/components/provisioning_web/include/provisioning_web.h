#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * HTTP without TLS is accepted only for the FW-1 LAN prototype
 * and is not the final product security model.
 */

#define PROVISIONING_WEB_MAX_BODY_BYTES 1024

esp_err_t provisioning_web_start(void);
esp_err_t provisioning_web_stop(void);
bool      provisioning_web_is_running(void);

typedef void (*provisioning_web_restart_fn_t)(void);

/**
 * Replace the default esp_restart() with a custom function.
 * Pass NULL to restore the default (esp_restart).
 * Used in tests to prevent actual device restart.
 */
void provisioning_web_set_restart_fn(provisioning_web_restart_fn_t fn);

#ifdef __cplusplus
}
#endif
