#!/usr/bin/env python3
import socket
import struct
import subprocess
import time
import unittest
import os
import signal
from enum import IntEnum
import sys
import threading

"""
Error handling test script for the netclocks program.
This script tests error handling capabilities by:
1. Sending invalid messages to a running program instance
2. Verifying appropriate error responses in stderr
3. Testing handling of various malformed/invalid messages
"""

from test_utils import (MessageType, LeaderState, capture_stderr, 
                        create_invalid_message, create_malformed_message,
                        send_and_assert_error, is_stderr_capture_active)

def output_reader(process, prefix):
    """Read output from a process and print it with a prefix"""
    # In text mode, readline() returns strings, not bytes
    for line in iter(process.stdout.readline, ''):
        sys.stdout.write(f"[{prefix}] {line}")
    
    # For stderr, check if stderr capture is active before reading
    while True:
        # Skip stderr reading if capture is active to avoid conflicts
        if not is_stderr_capture_active():
            try:
                line = process.stderr.readline()
                if not line:
                    # No more data, exit the loop
                    break
                sys.stderr.write(f"[{prefix}] {line}")
            except (IOError, ValueError):
                # Handle pipe errors or closed file
                break
        else:
            # If capture is active, wait briefly before checking again
            time.sleep(0.1)

class SingleNodeErrorTest(unittest.TestCase):
    """Error handling tests for a single node setup"""
    
    def setUp(self):
        """Setup test environment with a single program instance"""
        self.program_path = "../peer-time-sync"
        self.program_port = 12345
        self.program_address = "127.0.0.1"
        
        # Create a socket for sending/receiving
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2)  # 2 second timeout
        
        # Bind the socket to a specific port so we get consistent replies
        self.client_port = 54321
        self.sock.bind(('', self.client_port))
        
        # Start the program with correct parameters and capture output
        cmd = [self.program_path, "-p", str(self.program_port), "-b", self.program_address]
        self.program = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True
        )
        
        # Start output reader thread
        self.reader = threading.Thread(
            target=output_reader,
            args=(self.program, f"PROGRAM:{self.program_port}")
        )
        self.reader.daemon = True
        self.reader.start()
        
        # Wait a moment for the program to start
        time.sleep(1)
    
    def tearDown(self):
        """Clean up after test"""
        # Close the socket
        self.sock.close()
        
        # Terminate the program
        if self.program:
            self.program.terminate()
            try:
                self.program.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.program.kill()

    def send_message(self, message_type, content=None):
        """Send a message to the program"""
        message = bytes([message_type])
        
        if content is not None:
            message += content
        
        self.sock.sendto(message, (self.program_address, self.program_port))
    
    def receive_message(self, timeout=2):
        """Receive a message from the program"""
        self.sock.settimeout(timeout)
        try:
            data, addr = self.sock.recvfrom(65535)  # Max UDP packet size
            if data:
                msg_type = data[0] if data else None
                content = data[1:] if len(data) > 1 else None
                return (msg_type, content, addr)
            return None
        except socket.timeout:
            return None
            
    def create_sync_start_message(self, sync_level, timestamp):
        """Create a SYNC_START message"""
        # 1 byte sync level + 8 byte timestamp (big endian)
        return bytes([sync_level]) + struct.pack('!Q', timestamp)

    def test_invalid_message_type(self):
        """Test handling of invalid message type"""
        # Using our helper function to send invalid message and verify error
        output = send_and_assert_error(
            self, self.sock, self.program_address, self.program_port,
            create_invalid_message(), process=self.program
        )
        
        # You can add more specific assertions here
        self.assertIn("ERROR MSG", output.upper(), 
                      "Error should mention invalid message type")
    
    def test_malformed_sync_start(self):
        """Test handling of malformed SYNC_START message"""
        # Test with truncated message
        output = send_and_assert_error(
            self, self.sock, self.program_address, self.program_port,
            create_malformed_message(MessageType.SYNC_START, 'truncated'),
            process=self.program
        )
        self.assertIn("ERROR MSG", output.upper())
        
        # Test with wrong size content
        output = send_and_assert_error(
            self, self.sock, self.program_address, self.program_port,
            create_malformed_message(MessageType.SYNC_START, 'wrong_size'),
            process=self.program
        )
        self.assertIn("ERROR MSG", output.upper())
                      
    def test_unknown_sync_start(self):
        """Test that the program properly handles a sync start from an unconnected node."""
        with capture_stderr(self.program) as stderr_capture:
            # Send a sync_start to the node without connecting first
            sync_content = self.create_sync_start_message(0, 0)
            self.send_message(MessageType.SYNC_START, sync_content)

            # Check that the node does not respond to us
            response = self.receive_message(timeout=1)
            self.assertIsNone(response, "The node should not respond to a SYNC_START from someone they do not know")

            # And that it prints ERROR_MSG
            err_output = stderr_capture.get_output()
            self.assertIn("ERROR", err_output.upper(),
                   "The node should print an ERROR upon receiving a SYNC_START from an unknown node")

if __name__ == '__main__':
    unittest.main()