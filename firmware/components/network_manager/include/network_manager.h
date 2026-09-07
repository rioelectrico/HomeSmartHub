#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "esp_netif.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    NETWORK_EVENT_LINK_UP,
    NETWORK_EVENT_LINK_DOWN,
    NETWORK_EVENT_GOT_IP,
    NETWORK_EVENT_LOST_IP,
} network_event_type_t;

typedef void (*network_event_callback_t)(network_event_type_t event,
                                         const esp_netif_ip_info_t *ip,
                                         void *ctx);

/**
 * Register event handlers on the default event loop.
 * The default event loop must already exist (call esp_event_loop_create_default()
 * before this function).
 *
 * @param callback  Called from the event loop task when network state changes.
 * @param ctx       Opaque pointer passed back to callback.
 * @return ESP_OK, ESP_ERR_INVALID_STATE (already started), or error.
 */
esp_err_t network_manager_start(network_event_callback_t callback, void *ctx);

/**
 * Unregister event handlers. Safe to call even if not started.
 */
esp_err_t network_manager_stop(void);

/**
 * Returns true if a DHCP-assigned IP is currently held.
 */
bool network_manager_has_ip(void);

#ifdef __cplusplus
}
#endif
