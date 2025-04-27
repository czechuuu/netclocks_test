#!/usr/bin/env python3
import socket
import struct
import subprocess
import time
import unittest
import os
import signal
from enum import IntEnum
import ipaddress
import sys
import threading

"""
Test script for the netclocks program.
This script tests basic functionality by:
1. Starting the netclocks program
2. Sending various messages to it
3. Verifying the responses
"""

# Message types matching the C++ enum
class MessageType(IntEnum):
    INVALID = -1
    HELLO = 1
    HELLO_REPLY = 2
    CONNECT = 3
    ACK_CONNECT = 4
    SYNC_START = 11
    DELAY_REQUEST = 12
    DELAY_RESPONSE = 13
    LEADER = 21
    GET_TIME = 31
    TIME = 32

# Leader states matching the C++ enum
class LeaderState(IntEnum):
    LEADER_BEGIN = 0
    LEADER_STOP = 255

def output_reader(process, prefix):
    """Read output from a process and print it with a prefix"""
    # In text mode, readline() returns strings, not bytes
    for line in iter(process.stdout.readline, ''):
        sys.stdout.write(f"[{prefix}] {line}")
    
    for line in iter(process.stderr.readline, ''):
        sys.stderr.write(f"[{prefix}] {line}")

class NetclocksTest(unittest.TestCase):
    """Base class for netclocks tests"""
    
    def setUp(self):
        """Setup test environment"""
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
            text=True  # Use text mode instead of binary mode
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

class BasicTest(NetclocksTest):
    """Basic functionality tests"""
    
    def test_hello(self):
        """Test HELLO message and expect HELLO_REPLY"""
        # Send HELLO message
        self.send_message(MessageType.HELLO)
        
        # Expect HELLO_REPLY
        response = self.receive_message()
        self.assertIsNotNone(response, "No response received for HELLO message")
        
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.HELLO_REPLY, 
                         f"Expected HELLO_REPLY (2), got {msg_type}")
        
        # HELLO_REPLY should have exactly 3 bytes (type + node count)
        self.assertEqual(len(content) + 1, 3, 
                         "HELLO_REPLY message length is not 3 bytes")
        # Parse the node count from the response
        node_count = struct.unpack('!H', content)[0]
                
        # Verify node count is zero
        self.assertEqual(node_count, 0,
                         "HELLO_REPLY node count is not zero")

    def test_connect(self):
        """Test CONNECT message and expect ACK_CONNECT"""
        # Send CONNECT message
        self.send_message(MessageType.CONNECT)
        
        # Expect ACK_CONNECT
        response = self.receive_message()
        self.assertIsNotNone(response, "No response received for CONNECT message")
        
        msg_type, _, _ = response
        self.assertEqual(msg_type, MessageType.ACK_CONNECT, 
                         f"Expected ACK_CONNECT (4), got {msg_type}")

    def test_get_time(self):
        """Test GET_TIME message and expect TIME response"""
        # Send GET_TIME message
        self.send_message(MessageType.GET_TIME)
        
        # Expect TIME response
        response = self.receive_message()
        self.assertIsNotNone(response, "No response received for GET_TIME message")
        
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME, 
                         f"Expected TIME (32), got {msg_type}")
        
        # TIME message should have 9 bytes (type + sync_level + timestamp)
        self.assertEqual(len(content) + 1, 10, 
                        f"TIME message incorrect length, expected 10 bytes, got {len(content) + 1}")
        
        # Parse sync level and timestamp
        sync_level = content[0]
        timestamp = struct.unpack('!Q', content[1:9])[0]
        
        # Basic validation
        self.assertEqual(sync_level, 255, "Invalid sync level")
        self.assertGreaterEqual(timestamp, 0, "Invalid timestamp")

if __name__ == '__main__':
    unittest.main()