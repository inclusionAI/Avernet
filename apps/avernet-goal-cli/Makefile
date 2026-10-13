# Rust CLI 构建脚本

VERSION ?= $(shell cat VERSION)
HOST_TARGET := $(shell rustc -vV | awk '/host:/ {print $$2; exit}')
TARGET ?= $(HOST_TARGET)
BINARY_NAME ?= avernet-goal-cli
CARGO_BIN_NAME ?= $(shell awk -F'=' 'BEGIN { in_package = 0 } /^\[package\]/ { in_package = 1; next } /^\[/ && !/^\[package\]/ && in_package { exit } in_package && /^[[:space:]]*name[[:space:]]*=/ { gsub(/[ "]/, "", $$2); print $$2; exit }' Cargo.toml)
DIST_DIR := dist

ifneq ($(strip $(BUILD_OS)),)
ifneq ($(strip $(BUILD_ARCH)),)
ifeq ($(BUILD_OS)-$(BUILD_ARCH),linux-amd64)
TARGET := x86_64-unknown-linux-musl
else ifeq ($(BUILD_OS)-$(BUILD_ARCH),linux-arm64)
TARGET := aarch64-unknown-linux-musl
else ifeq ($(BUILD_OS)-$(BUILD_ARCH),darwin-amd64)
TARGET := x86_64-apple-darwin
else ifeq ($(BUILD_OS)-$(BUILD_ARCH),darwin-arm64)
TARGET := aarch64-apple-darwin
else ifeq ($(BUILD_OS)-$(BUILD_ARCH),windows-amd64)
TARGET := x86_64-pc-windows-gnu
else
$(error unsupported BUILD_OS/BUILD_ARCH combination: $(BUILD_OS)/$(BUILD_ARCH))
endif
else
$(error BUILD_ARCH is required when BUILD_OS is set)
endif
else ifneq ($(strip $(BUILD_ARCH)),)
$(error BUILD_OS is required when BUILD_ARCH is set)
endif

ifeq ($(findstring apple-darwin,$(TARGET)),apple-darwin)
	BUILD_OS := darwin
else ifeq ($(findstring windows,$(TARGET)),windows)
	BUILD_OS := windows
else
	BUILD_OS := linux
endif

ifeq ($(findstring x86_64,$(TARGET)),x86_64)
	BUILD_ARCH := amd64
else ifeq ($(findstring aarch64,$(TARGET)),aarch64)
	BUILD_ARCH := arm64
else
	BUILD_ARCH := $(TARGET)
endif

ifeq ($(BUILD_OS),windows)
	BINARY_SUFFIX := .exe
else
	BINARY_SUFFIX :=
endif

CARGO_TARGET_DIR ?= target/$(BUILD_OS)-$(BUILD_ARCH)
RELEASE_DIR := $(CARGO_TARGET_DIR)/$(TARGET)/release
SOURCE_BINARY := $(RELEASE_DIR)/$(CARGO_BIN_NAME)$(BINARY_SUFFIX)
BINARY_OUTPUT_NAME := $(BINARY_NAME)-$(BUILD_OS)-$(BUILD_ARCH)-$(VERSION)$(BINARY_SUFFIX)

.PHONY: help build build-linux-amd64 build-linux-arm64 build-darwin-amd64 build-darwin-arm64 build-windows-amd64 clean

help:
	@echo "可用命令:"
	@echo "  make build                    # 构建当前宿主机平台产物"
	@echo "  make build-linux-amd64        # 构建 Linux amd64"
	@echo "  make build-linux-arm64        # 构建 Linux arm64"
	@echo "  make build-darwin-amd64       # 构建 macOS amd64"
	@echo "  make build-darwin-arm64       # 构建 macOS arm64"
	@echo "  make build-windows-amd64      # 构建 Windows amd64"
	@echo "  make clean                    # 清理 dist 和 target"
	@echo
	@echo "可选参数:"
	@echo "  VERSION=<version>             # 默认读取 VERSION 文件"
	@echo "  BINARY_NAME=<name>            # 发布产物文件名，默认 avernet-goal-cli"
	@echo "  CARGO_BIN_NAME=<name>         # Cargo 实际产物名，默认读取 Cargo.toml package.name"
	@echo "  TARGET=<triple>               # 直接指定 Rust target triple"
	@echo "  CARGO_TARGET_DIR=<dir>        # Cargo 目标目录，默认 target/<os>-<arch>"
	@echo "  BUILD_OS=<os> BUILD_ARCH=<arch> # 类似 Go 风格，例如 linux/amd64"

build:
	@echo "构建 $(BINARY_NAME)..."
	@echo "  Cargo 二进制名: $(CARGO_BIN_NAME)"
	@echo "  目标平台: $(TARGET)"
	@echo "  BUILD_OS/ARCH: $(BUILD_OS)/$(BUILD_ARCH)"
	@echo "  版本号: $(VERSION)"
	@echo "  Cargo target dir: $(CARGO_TARGET_DIR)"
	@echo "  输出文件: $(DIST_DIR)/$(BINARY_OUTPUT_NAME)"
	cargo build --release --target $(TARGET) --target-dir $(CARGO_TARGET_DIR)
	mkdir -p $(DIST_DIR)
	cp $(SOURCE_BINARY) $(DIST_DIR)/$(BINARY_OUTPUT_NAME)

build-linux-amd64:
	$(MAKE) build BUILD_OS=linux BUILD_ARCH=amd64

build-linux-arm64:
	$(MAKE) build BUILD_OS=linux BUILD_ARCH=arm64

build-darwin-amd64:
	$(MAKE) build BUILD_OS=darwin BUILD_ARCH=amd64

build-darwin-arm64:
	$(MAKE) build BUILD_OS=darwin BUILD_ARCH=arm64

build-windows-amd64:
	$(MAKE) build BUILD_OS=windows BUILD_ARCH=amd64

clean:
	rm -rf $(DIST_DIR) $(CARGO_TARGET_DIR)
