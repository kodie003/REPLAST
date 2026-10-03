#!/usr/bin/env bash
# Compile and run the firmware host test. Needs g++ (sudo apt install g++).
# Expected last line: ALL OK
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -I. -Wall -Wextra -Wno-unused-parameter -o /tmp/replast_host_test host_test.cpp
/tmp/replast_host_test
