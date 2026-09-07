idf_component_register(SRCS "app_main.c" "test_device_auth.c" "test_device_config.c"
                             "test_portero_codec.c" "test_network_events.c"
                             "test_provisioning_web.c"
                       REQUIRES unity device_auth device_config portero_codec
                                network_manager provisioning_web
                                esp_http_server esp_http_client)
