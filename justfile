#!/usr/bin/env just --justfile

test:
    @echo "Running coverage-gated tests for all Python modules..."
    @cd src/backend && just test
    @cd src/engine && just test
    @cd src/baas && just test
    @cd src/gateway && just test

test-no-cov:
    @echo "Running fast tests (no coverage gate) for all Python modules..."
    @cd src/backend && just test-no-cov
    @cd src/engine && just test-no-cov
    @cd src/baas && just test-no-cov
    @cd src/gateway && just test-no-cov