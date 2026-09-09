idf_component_register(
    SRCS "app_conversation.c"
    INCLUDE_DIRS "include"
    REQUIRES
        board_audio
        ws_transport
        portero_codec
        paud_codec
        freertos
        log
)
