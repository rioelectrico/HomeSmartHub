cmake_minimum_required(VERSION 3.16)

# The protocol component has no board dependency, so this test application can
# be built and flashed independently once a supported ESP-IDF environment and
# test board are selected.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components/device_protocol")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(device_protocol_test_app)
