# NetClocks Tests

This directory contains test scripts for validating the NetClocks implementation.

## Setup

1. Make sure you have built the NetClocks binary
2. Place the binary in the parent directory of this tests folder
   - The binary should be named `peer-time-sync` and located at: `../peer-time-sync` (relative to this tests directory)

## Running Tests

You can run all tests using:

```bash
python3 run_tests.py
```

Or run individual test files:

```bash
python3 test_basic.py
python3 test_sync.py
```

## Test Files

- `test_basic.py`: Basic functionality tests
- `test_sync.py`: Clock synchronization tests

## Requirements

- Python 3.6+
- The `peer-time-sync` binary must be compiled and placed in the parent directory
- Tests expect the binary to follow the interface as specified in the project requirements

## Troubleshooting

If tests are failing, ensure:
- The binary is correctly placed in the parent directory and named `peer-time-sync`
- The binary has executable permissions (`chmod +x ../peer-time-sync`)
- Your implementation correctly follows the protocol specification