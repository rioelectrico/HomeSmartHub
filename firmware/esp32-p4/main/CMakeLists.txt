idf_component_register(SRCS "app_main.c"
                       REQUIRES device_protocol hardware_hal
                                board_port network_manager provisioning_web
                                device_config
                       PRIV_REQUIRES esp_common esp_hw_support log
                                     nvs_flash)
