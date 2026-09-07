#pragma once

#include "esp_err.h"
#include "esp_eth.h"
#include "esp_netif.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize Waveshare ESP32-P4-ETH board Ethernet hardware.
 *
 * Configures EMAC/IP101GR RMII using the GPIOs defined in the board schematic,
 * installs the Ethernet driver, creates the default Ethernet netif and attaches
 * the netif glue. The caller must start the event loop before calling this
 * function and must call esp_eth_start(eth_handle) afterwards.
 *
 * @param[out] out_eth_handle  Ethernet driver handle.
 * @param[out] out_netif       esp_netif instance for the Ethernet interface.
 * @return ESP_OK on success, error code otherwise.
 */
esp_err_t board_port_init(esp_eth_handle_t *out_eth_handle, esp_netif_t **out_netif);

#ifdef __cplusplus
}
#endif
