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
  ./run_tests.py -v            # Run with verbose output
"""

def main():
    parser = argparse.ArgumentParser(description='Run netclock tests')
    parser.add_argument('-b', '--basic', action='store_true', 
                        help='Run only basic tests')
    parser.add_argument('-s', '--sync', action='store_true', 
                        help='Run only synchronization tests')
    parser.add_argument('-v', '--verbose', action='store_true', 
                        help='Verbose output')
    args = parser.parse_args()
    
    # Discover tests
    loader = unittest.TestLoader()
    
    # Determine which tests to run
    test_suite = unittest.TestSuite()
    
    if args.basic or (not args.basic and not args.sync):
        # Add basic tests
        print("Including basic tests...")
        from test_basic import BasicTest
        basic_tests = loader.loadTestsFromTestCase(BasicTest)
        test_suite.addTest(basic_tests)
    
    if args.sync or (not args.basic and not args.sync):
        # Add sync tests
        print("Including synchronization tests...")
        from test_sync import SyncTest
        sync_tests = loader.loadTestsFromTestCase(SyncTest)
        test_suite.addTest(sync_tests)
    
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
    os.chmod(__file__, 0o755)
    
    main()