cmake_minimum_required(VERSION 3.22.1)
project(wa_jni LANGUAGES CXX)

add_library(wa_jni SHARED
    wa_jni.cpp
)

find_library(log-lib log)

target_link_libraries(wa_jni
    ${log-lib}
    dl
)
