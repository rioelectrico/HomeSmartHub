#include "unity.h"

extern void run_device_auth_tests(void);
extern void run_device_config_tests(void);
extern void run_portero_codec_tests(void);
extern void run_network_events_tests(void);

void app_main(void)
{
    UNITY_BEGIN();
    run_device_auth_tests();
    run_device_config_tests();
    run_portero_codec_tests();
    run_network_events_tests();
    UNITY_END();
}
