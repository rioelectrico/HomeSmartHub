idf_component_register(SRCS "network_manager.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_eth esp_netif esp_event esp_common log)
