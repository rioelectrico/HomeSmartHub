#include "board_port.h"
#include "esp_eth.h"
#include "esp_eth_mac_esp.h"
#include "esp_eth_phy.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "esp_check.h"

static const char *TAG = "board_port";

/*
 * Waveshare ESP32-P4-ETH schematic verified pinout.
 * ETH_ESP32_EMAC_DEFAULT_CONFIG() for CONFIG_IDF_TARGET_ESP32P4 sets these
 * values automatically:
 *   MDC=31, MDIO=52, CLK_IN=50, TX_EN=49, TXD0=34, TXD1=35,
 *   CRS_DV=28, RXD0=29, RXD1=30
 * Only PHY address and reset pin are board-specific overrides.
 */
#define BOARD_ETH_PHY_ADDR    1
#define BOARD_ETH_RESET_GPIO  51

esp_err_t board_port_init(esp_eth_handle_t *out_eth_handle,
                           esp_netif_t    **out_netif)
{
    if (!out_eth_handle || !out_netif) return ESP_ERR_INVALID_ARG;

    /* Netif */
    esp_netif_config_t netif_cfg = ESP_NETIF_DEFAULT_ETH();
    esp_netif_t *eth_netif = esp_netif_new(&netif_cfg);
    ESP_RETURN_ON_FALSE(eth_netif, ESP_ERR_NO_MEM, TAG, "netif alloc failed");

    /* MAC — default config already has all ESP32-P4 GPIO values */
    eth_esp32_emac_config_t emac_config = ETH_ESP32_EMAC_DEFAULT_CONFIG();
    eth_mac_config_t        mac_config  = ETH_MAC_DEFAULT_CONFIG();
    esp_eth_mac_t *mac = esp_eth_mac_new_esp32(&emac_config, &mac_config);
    ESP_RETURN_ON_FALSE(mac, ESP_FAIL, TAG, "MAC alloc failed");

    /* PHY — IP101GR, address 1, reset on GPIO 51 */
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    phy_config.phy_addr      = BOARD_ETH_PHY_ADDR;
    phy_config.reset_gpio_num = BOARD_ETH_RESET_GPIO;
    esp_eth_phy_t *phy = esp_eth_phy_new_ip101(&phy_config);
    ESP_RETURN_ON_FALSE(phy, ESP_FAIL, TAG, "PHY alloc failed");

    /* Driver */
    esp_eth_config_t  eth_config = ETH_DEFAULT_CONFIG(mac, phy);
    esp_eth_handle_t  eth_handle = NULL;
    ESP_RETURN_ON_ERROR(esp_eth_driver_install(&eth_config, &eth_handle),
                        TAG, "Ethernet driver install failed");

    /* Attach netif glue */
    esp_eth_netif_glue_handle_t glue = esp_eth_new_netif_glue(eth_handle);
    ESP_RETURN_ON_FALSE(glue, ESP_FAIL, TAG, "netif glue alloc failed");
    ESP_RETURN_ON_ERROR(esp_netif_attach(eth_netif, glue),
                        TAG, "netif attach failed");

    ESP_LOGI(TAG, "IP101GR RMII ready (PHY addr=%d, reset GPIO=%d)",
             BOARD_ETH_PHY_ADDR, BOARD_ETH_RESET_GPIO);

    *out_eth_handle = eth_handle;
    *out_netif      = eth_netif;
    return ESP_OK;
}
