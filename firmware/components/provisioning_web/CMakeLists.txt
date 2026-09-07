idf_component_register(SRCS "provisioning_web.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_http_server device_config esp_common log
                       PRIV_REQUIRES mbedtls esp_system freertos)
