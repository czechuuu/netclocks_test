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
Advanced test script for the netclocks program.
This script tests synchronization functionality by:
1. Starting two instances of the program
2. Testing leader election
3. Testing time synchronization between instances
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

class SyncTest(unittest.TestCase):
    """Tests for synchronization functionality"""
    
    def setUp(self):
        """Setup test environment with two program instances"""
        self.program_path = "../peer-time-sync"
        
        # First instance configuration
        self.program1_port = 12345
        self.program1_address = "127.0.0.1"
        
        # Second instance configuration
        self.program2_port = 12346
        self.program2_address = "127.0.0.1"
        
        # Create sockets for sending/receiving
        self.sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock1.settimeout(2)  # 2 second timeout
        self.client1_port = 54321
        self.sock1.bind(('', self.client1_port))
        
        self.sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock2.settimeout(2)  # 2 second timeout
        self.client2_port = 54322
        self.sock2.bind(('', self.client2_port))
        
        # Start the first program with output prefixing - Fixed buffering warning by using text mode
        cmd1 = [self.program_path, "-p", str(self.program1_port), "-b", self.program1_address]
        self.program1 = subprocess.Popen(
            cmd1, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            bufsize=1,
            text=True  # Use text mode instead of binary mode
        )
        
        # Start output reader thread for first program
        self.reader1 = threading.Thread(
            target=output_reader,
            args=(self.program1, f"PROGRAM1:{self.program1_port}")
        )
        self.reader1.daemon = True
        self.reader1.start()
        
        # Wait a moment for the first program to start
        time.sleep(1)
        
        # Start the second program and connect it to the first, with output prefixing
        cmd2 = [
            self.program_path,
            "-p", str(self.program2_port),
            "-b", self.program2_address,
            "-r", str(self.program1_port),
            "-a", self.program1_address
        ]
        self.program2 = subprocess.Popen(
            cmd2, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            bufsize=1,
            text=True  # Use text mode instead of binary mode
        )
        
        # Start output reader thread for second program
        self.reader2 = threading.Thread(
            target=output_reader,
            args=(self.program2, f"PROGRAM2:{self.program2_port}")
        )
        self.reader2.daemon = True
        self.reader2.start()
        
        # Wait a moment for the second program to start and connect
        time.sleep(2)
    
    def tearDown(self):
        """Clean up after test"""
        # Close the sockets
        self.sock1.close()
        self.sock2.close()
        
        # Terminate the programs
        if hasattr(self, 'program1') and self.program1:
            self.program1.terminate()
            try:
                self.program1.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.program1.kill()
                
        if hasattr(self, 'program2') and self.program2:
            self.program2.terminate()
            try:
                self.program2.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.program2.kill()

    def send_message(self, sock, address, port, message_type, content=None):
        """Send a message to the program"""
        message = bytes([message_type])
        
        if content is not None:
            message += content
        
        sock.sendto(message, (address, port))
    
    def receive_message(self, sock, timeout=2):
        """Receive a message from the program"""
        sock.settimeout(timeout)
        try:
            data, addr = sock.recvfrom(65535)  # Max UDP packet size
            if data:
                msg_type = data[0] if data else None
                content = data[1:] if len(data) > 1 else None
                return (msg_type, content, addr)
            return None
        except socket.timeout:
            return None

    def create_leader_message(self, state):
        """Create a leader message with the given state"""
        return bytes([int(state)])

    def create_sync_start_message(self, sync_level, timestamp):
        """Create a SYNC_START message"""
        # 1 byte sync level + 8 byte timestamp (big endian)
        return bytes([sync_level]) + struct.pack('!Q', timestamp)

    def test_get_time_both_instances(self):
        """Test that both instances respond to GET_TIME"""
        # Send GET_TIME to first instance
        self.send_message(self.sock1, self.program1_address, self.program1_port, 
                          MessageType.GET_TIME)
        
        # Expect TIME response from first instance
        response1 = self.receive_message(self.sock1)
        self.assertIsNotNone(response1, "No response from first instance")
        
        msg_type1, content1, _ = response1
        self.assertEqual(msg_type1, MessageType.TIME, 
                         f"Expected TIME (32) from first instance, got {msg_type1}")
        
        # Send GET_TIME to second instance
        self.send_message(self.sock2, self.program2_address, self.program2_port, 
                          MessageType.GET_TIME)
        
        # Expect TIME response from second instance
        response2 = self.receive_message(self.sock2)
        self.assertIsNotNone(response2, "No response from second instance")
        
        msg_type2, content2, _ = response2
        self.assertEqual(msg_type2, MessageType.TIME, 
                         f"Expected TIME (32) from second instance, got {msg_type2}")
        
        # Parse timestamps
        sync_level1 = content1[0]
        timestamp1 = struct.unpack('!Q', content1[1:9])[0]
        
        sync_level2 = content2[0]
        timestamp2 = struct.unpack('!Q', content2[1:9])[0]

        # They should both be unsynchronized
        self.assertEqual(sync_level1, 255, "First instance should not be synchronized")
        self.assertEqual(sync_level2, 255, "Second instance should not be synchronized")

        # Check that the timestamps have approx. correct values
        self.assertAlmostEqual(timestamp1, 3000, delta=100, msg="First instance timestamp is not close to 2000")
        self.assertAlmostEqual(timestamp2, 2000, delta=100, msg="Second instance timestamp is not close to 3000")



    def test_leader_election(self):
        """Test sending leader messages"""
        # Connect to establish connections with sock2
        self.send_message(self.sock2, self.program1_address, self.program1_port, 
                          MessageType.CONNECT)
        # Discard the ACK_CONNECT
        _ = self.receive_message(self.sock2)

        # Send LEADER_BEGIN message
        leader_content = self.create_leader_message(LeaderState.LEADER_BEGIN)
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.LEADER, leader_content)
        
        # Give some time for leader election to take effect
        time.sleep(3)

        # Now we should receive a SYNC_START message from the program who just became the leader
        response = self.receive_message(self.sock2)
        self.assertIsNotNone(response, "No response received after leader election")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.SYNC_START,
                         f"Expected SYNC_START (11), got {msg_type}")
        
        # Parse the SYNC_START message
        sync_level = content[0]
        self.assertEqual(sync_level, 0, "Invalid sync level in SYNC_START message from leader")
        
        # Now send GET_TIME to the first instance to check if it is the leader
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.GET_TIME)
        
        # Get the TIME response
        response = self.receive_message(self.sock1)
        self.assertIsNotNone(response, "No response received after leader election")
        
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME)
        
        # We should see that the sync level is 0, indicating that this node is the leader
        sync_level = content[0]
        self.assertTrue(sync_level == 0, "The node should become a leader")

        # Now send GET_TIME to the second instance to check if it is not the leader
        # but it should be synchronized with the first instance 
        self.send_message(self.sock1, self.program2_address, self.program2_port,
                            MessageType.GET_TIME)
        
        # Get the TIME response
        response = self.receive_message(self.sock1)
        self.assertIsNotNone(response, "No response received from second instance")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME)

        # We should see that the sync level is 1, indicating that this node is not the leader
        sync_level = content[0]
        self.assertTrue(sync_level == 1, "The node should be directly connected to the leader")
        
        # Now send LEADER_STOP
        leader_content = self.create_leader_message(LeaderState.LEADER_STOP)
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.LEADER, leader_content)

        # Wait a moment for the leader to stop
        time.sleep(1)
        
        # Now send another GET_TIME to verify that the node is no longer a leader
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.GET_TIME)
            
        # Get the TIME response
        response = self.receive_message(self.sock1)
        self.assertIsNotNone(response, "No response received after leader stop")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME)

        # We should see that the sync level is not 0 anymore, indicating that this node is not a leader
        sync_level = content[0]
        self.assertTrue(sync_level != 0, "The node should not be a leader anymore")

    def test_sync_start(self):
        """Test the SYNC_START message processing"""
        # Connect to establish relationship with the first instance
        self.send_message(self.sock1, self.program1_address, self.program1_port, 
                          MessageType.CONNECT)
        _ = self.receive_message(self.sock1)  # Discard ACK_CONNECT
        
        # Create and send a SYNC_START message to first instance
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        sync_content = self.create_sync_start_message(0, current_time)
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.SYNC_START, sync_content)
        
        # Wait for the DELAY_REQUEST
        response = self.receive_message(self.sock1, timeout=3)
        self.assertIsNotNone(response, "No response received for SYNC_START message")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.DELAY_REQUEST,
                         f"Expected DELAY_REQUEST (12), got {msg_type}")

        # Send a DELAY_RESPONSE with current time
        current_time = int(time.time() * 1000)
        delay_response_content = self.create_sync_start_message(0, current_time)
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                            MessageType.DELAY_RESPONSE, delay_response_content)
        
        # Wait for the SYNC_START message, when we get it, we should know that the node has synchronized with us
        response = self.receive_message(self.sock1, timeout = 10)
        self.assertIsNotNone(response, "Node didn't send a SYNC_START in 10 seconds")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.SYNC_START,
                         f"Expected SYNC_START (11), got {msg_type}")
        sync_level = content[0]
        self.assertEqual(sync_level, 1, "Invalid sync level in SYNC_START message")
        # Check that the timestamp is close to the current time
        timestamp = struct.unpack('!Q', content[1:9])[0]
        current_time = int(time.time() * 1000)
        self.assertAlmostEqual(timestamp, current_time, delta=100, 
                               msg="Timestamp in SYNC_START message is not close to current time")

        # Now we'll send a GET_TIME message to the second instance,
        # to see whether it has synchronized with the first instance
        # But first, we need to wait a moment for the SYNC_START to be processed
        time.sleep(1)
        self.send_message(self.sock2, self.program2_address, self.program2_port,
                          MessageType.GET_TIME)
        
        # Get the TIME response
        response = self.receive_message(self.sock2)
        self.assertIsNotNone(response, "No response received for GET_TIME message")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME,
                         f"Expected TIME (32), got {msg_type}")
        # We should see that the sync level is 2, indicating that this node is synchronized with the first instance
        sync_level = content[0]
        self.assertTrue(sync_level == 2, "The node should be synchronized with the first instance")
        # Check that the timestamp is close to the current time
        timestamp = struct.unpack('!Q', content[1:9])[0]
        current_time = int(time.time() * 1000)
        self.assertAlmostEqual(timestamp, current_time, delta=100, 
                               msg="Timestamp in TIME message is not close to current time")

if __name__ == '__main__':
    unittest.main()