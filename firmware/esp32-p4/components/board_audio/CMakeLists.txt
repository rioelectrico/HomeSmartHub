idf_component_register(
    SRCS "board_audio.c"
    INCLUDE_DIRS "include"
    REQUIRES
        hardware_hal
        esp_driver_i2s
        esp_driver_i2c
        esp_driver_gpio
        espressif__esp_codec_dev
        freertos
        log
)
