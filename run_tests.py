#!/usr/bin/env python3
import unittest
import argparse
import sys
import os

"""
Test runner script for netclock tests.
This script discovers and runs all test cases or specific test groups.

Usage:
  ./run_tests.py               # Run all tests
  ./run_tests.py -b            # Run only basic tests
  ./run_tests.py -s            # Run only sync tests
  ./run_tests.py -e            # Run only error tests
  ./run_tests.py -v            # Run with verbose output
"""

def main():
    parser = argparse.ArgumentParser(description='Run netclock tests')
    parser.add_argument('-b', '--basic', action='store_true', 
                        help='Run only basic tests')
    parser.add_argument('-s', '--sync', action='store_true', 
                        help='Run only synchronization tests')
    parser.add_argument('-e', '--error', action='store_true',
                        help='Run only error handling tests')
    parser.add_argument('-v', '--verbose', action='store_true', 
                        help='Verbose output')
    args = parser.parse_args()
    
    # Discover tests
    loader = unittest.TestLoader()
    
    # Determine which tests to run
    test_suite = unittest.TestSuite()
    
    # If no specific test type is selected, run all tests
    run_all = not (args.basic or args.sync or args.error)
    
    if args.basic or run_all:
        # Add basic tests
        print("Including basic tests...")
        from test_basic import BasicTest
        basic_tests = loader.loadTestsFromTestCase(BasicTest)
        test_suite.addTest(basic_tests)
    
    if args.sync or run_all:
        # Add sync tests
        print("Including synchronization tests...")
        from test_sync import SyncTest
        sync_tests = loader.loadTestsFromTestCase(SyncTest)
        test_suite.addTest(sync_tests)
        
    if args.error or run_all:
        # Add error handling tests
        print("Including error handling tests...")
        from test_error import SingleNodeErrorTest
        single_node_error_tests = loader.loadTestsFromTestCase(SingleNodeErrorTest)
        test_suite.addTest(single_node_error_tests)
    
    # Run the tests
    verbosity = 2 if args.verbose else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(test_suite)
    
    # Return appropriate exit code
    sys.exit(not result.wasSuccessful())

if __name__ == '__main__':
    # Check if the C++ program is built
    program_path = "../peer-time-sync"
    if not os.path.isfile(program_path):
        print("Error: The netclocks program is not built.")
        print("Please run 'make' in the parent directory first.")
        sys.exit(1)
        
    # Make sure Python scripts are executable
    os.chmod("test_basic.py", 0o755)
    os.chmod("test_sync.py", 0o755)
    os.chmod("test_error.py", 0o755)
    os.chmod(__file__, 0o755)
    
    main()