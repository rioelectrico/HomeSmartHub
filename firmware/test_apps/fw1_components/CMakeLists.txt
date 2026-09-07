cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../../components/device_auth"
    "${CMAKE_CURRENT_LIST_DIR}/../../components/device_config"
    "${CMAKE_CURRENT_LIST_DIR}/../../components/portero_codec"
    "${CMAKE_CURRENT_LIST_DIR}/../../components/network_manager"
    "${CMAKE_CURRENT_LIST_DIR}/../../components/provisioning_web")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(fw1_components_test_app)
