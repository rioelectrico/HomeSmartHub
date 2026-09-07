idf_component_register(SRCS "ws_transport.c"
                       INCLUDE_DIRS "include"
                       REQUIRES portero_codec device_auth device_config esp_common log
                       PRIV_REQUIRES espressif__esp_websocket_client mbedtls freertos
                                     esp_timer json)
