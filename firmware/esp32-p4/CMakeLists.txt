cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../components"
    "${CMAKE_CURRENT_LIST_DIR}/components/board_port"
    "${CMAKE_CURRENT_LIST_DIR}/components/board_audio")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(portero_esp32_p4)
