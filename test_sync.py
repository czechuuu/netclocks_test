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
1. Starting instances of the program
2. Testing leader election
3. Testing time synchronization between instances

Tests are separated into two classes:
- SingleNodeSyncTest: Tests that require only one program instance
- MultiNodeSyncTest: Tests that require multiple program instances interacting
"""

from test_utils import MessageType, LeaderState, capture_stderr, is_stderr_capture_active

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

class SingleNodeSyncTest(unittest.TestCase):
    """Tests for synchronization functionality with a single program instance"""
    
    def setUp(self):
        """Setup test environment with a single program instance"""
        self.program_path = "../peer-time-sync"
        
        # Program configuration
        self.program_port = 12345
        self.program_address = "127.0.0.1"
        
        # Create socket for sending/receiving
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2)  # 2 second timeout
        self.client_port = 54321
        self.sock.bind(('', self.client_port))
        
        # Start program with output prefixing
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
        if hasattr(self, 'program') and self.program:
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

    def create_leader_message(self, state):
        """Create a leader message with the given state"""
        return bytes([int(state)])

    def create_sync_start_message(self, sync_level, timestamp):
        """Create a SYNC_START message"""
        # 1 byte sync level + 8 byte timestamp (big endian)
        return bytes([sync_level]) + struct.pack('!Q', timestamp)

    def test_sync_start(self):
        """Test the SYNC_START message processing for a single node"""
        # Connect to establish relationship with the program
        self.send_message(MessageType.CONNECT)
        _ = self.receive_message()  # Discard ACK_CONNECT
        
        # Create and send a SYNC_START message
        current_time = int(time.time() * 1000)  # Current time in milliseconds
        sync_content = self.create_sync_start_message(0, current_time)
        self.send_message(MessageType.SYNC_START, sync_content)
        
        # Wait for the DELAY_REQUEST
        response = self.receive_message(timeout=3)
        self.assertIsNotNone(response, "No response received for SYNC_START message")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.DELAY_REQUEST,
                         f"Expected DELAY_REQUEST (12), got {msg_type}")

        # Send a DELAY_RESPONSE with current time
        current_time = int(time.time() * 1000)
        delay_response_content = self.create_sync_start_message(0, current_time)
        self.send_message(MessageType.DELAY_RESPONSE, delay_response_content)
        
        # Wait for the SYNC_START message, when we get it, we should know that the node has synchronized with us
        response = self.receive_message(timeout=10)
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

    def test_changed_sync_level(self):
        """Test that the node recognises a synchronization source 
        changing its sync level between SYNC_START and DELAY_RESPONSE as an error"""
        with capture_stderr(self.program) as stderr_capture:
            # Connect to the node
            self.send_message(MessageType.CONNECT)
            # Discard the ACK_CONNECT
            _ = self.receive_message()

            # Send a sync start to the node
            sync_content = self.create_sync_start_message(0, 0)
            self.send_message(MessageType.SYNC_START, sync_content)

            # Wait for the DELAY_REQUEST
            delay_rq = self.receive_message()
            self.assertIsNotNone(delay_rq, "No response received for SYNC_START message")
            msg_type, content, _ = delay_rq
            self.assertEqual(msg_type, MessageType.DELAY_REQUEST,
                             f"Expected DELAY_REQUEST (12), got {msg_type}")

            # Send a DELAY_RESPONSE with sync level 1
            delay_response_content = self.create_sync_start_message(1, 0)
            self.send_message(MessageType.DELAY_RESPONSE, delay_response_content)
            
            # Wait a bit for the node to process the message
            time.sleep(1)
            
            # Ensure the node prints an error message
            err_output = stderr_capture.get_output()
            self.assertIn("ERROR MSG", err_output.upper(),
                   "The node should print an ERROR upon receiving a DELAY_RESPONSE with a different sync level")

            # And that its still not synchronized
            self.send_message(MessageType.GET_TIME)
            # Get the TIME response
            response = self.receive_message()
            self.assertIsNotNone(response, "No response received for GET_TIME message")
            msg_type, content, _ = response
            self.assertEqual(msg_type, MessageType.TIME,
                             f"Expected TIME (32), got {msg_type}")
            # We should see that the sync level is 255, indicating that this node is not synchronized
            sync_level = content[0]
            self.assertTrue(sync_level == 255, "The node should not be synchronized after an unsuccessful sync")
    
    def test_incoming_sync_timeout(self):
        """Test that the node times out if it doesn't receive a DELAY_RESPONSE in time"""
        with capture_stderr(self.program) as stderr_capture:
            # Connect to the node
            self.send_message(MessageType.CONNECT)
            # Discard the ACK_CONNECT
            _ = self.receive_message()

            # Send a sync start to the node
            sync_content = self.create_sync_start_message(0, 0)
            self.send_message(MessageType.SYNC_START, sync_content)

            # Wait for the DELAY_REQUEST
            delay_rq = self.receive_message()
            self.assertIsNotNone(delay_rq, "No response received for SYNC_START message")
            msg_type, content, _ = delay_rq
            self.assertEqual(msg_type, MessageType.DELAY_REQUEST,
                             f"Expected DELAY_REQUEST (12), got {msg_type}")

            # Wait for a while without sending a DELAY_RESPONSE
            print("Going to sleep for 10 secs - be patient")
            time.sleep(10)

            # Reply after the delay
            delay_response_content = self.create_sync_start_message(0, 0)
            self.send_message(MessageType.DELAY_RESPONSE, delay_response_content)
            
            # Wait a bit for the node to process the message
            time.sleep(1)
            # Ensure the node prints an error message
            err_output = stderr_capture.get_output()
            self.assertIn("ERROR MSG", err_output.upper(),
                   "The node should print an ERROR receiving a DELAY_RESPONSE after a timeout")

    def test_outcoming_sync_timeout(self):
        """Test handling of outgoing synchronization timeout"""
        with capture_stderr(self.program) as stderr_capture:
            # Connect to the node
            self.send_message(MessageType.CONNECT)
            # Discard the ACK_CONNECT
            _ = self.receive_message()

            # Send a LEADER_START to the node
            leader_content = self.create_leader_message(LeaderState.LEADER_BEGIN)
            self.send_message(MessageType.LEADER, leader_content)
            
            # Wait to receive the SYNC_START
            response = self.receive_message(timeout=3)
            self.assertIsNotNone(response, "No response received for LEADER message")
            msg_type, content, _ = response
            self.assertEqual(msg_type, MessageType.SYNC_START,
                             f"Expected SYNC_START (11), got {msg_type}")
            
            # Now take back the leader status
            leader_content = self.create_leader_message(LeaderState.LEADER_STOP)
            self.send_message(MessageType.LEADER, leader_content)
            
            # Wait for a while for the node to process the message
            time.sleep(1)

            # Send a DELAY_REQUEST to the node
            self.send_message(MessageType.DELAY_REQUEST)
            
            # Receive the DELAY_RESPONSE
            response = self.receive_message(timeout=3)
            self.assertIsNotNone(response, "No response received for DELAY_REQUEST message")
            msg_type, content, _ = response
            self.assertEqual(msg_type, MessageType.DELAY_RESPONSE,
                             f"Expected DELAY_RESPONSE (12), got {msg_type}")
            
            # We should see that the sync level is 255, indicating that this node is not synchronized
            sync_level = content[0]
            self.assertTrue(sync_level == 255, "The node should not be synchronized after taking away its leader status")


class MultiNodeSyncTest(unittest.TestCase):
    """Tests for synchronization functionality between multiple program instances"""
    
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
        
        # Start the first program with output prefixing
        cmd1 = [self.program_path, "-p", str(self.program1_port), "-b", self.program1_address]
        self.program1 = subprocess.Popen(
            cmd1, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            bufsize=1,
            text=True
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
        
        # Start the second program and connect it to the first
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
            text=True
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
        self.assertGreater(sync_level, 0, "The node should not be a leader anymore")
        
    def test_inter_node_synchronization(self):
        """Test synchronization between two program instances"""
        # Connect to the first instance
        self.send_message(self.sock1, self.program1_address, self.program1_port,
                          MessageType.CONNECT)
        # Discard the ACK_CONNECT
        _ = self.receive_message(self.sock1)

        # Send a SYNC_START message to first instance
        current_time = int(time.time() * 1000)
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
        
        # Wait for the SYNC_START message
        response = self.receive_message(self.sock1, timeout=10)
        self.assertIsNotNone(response, "Node didn't send a SYNC_START in 10 seconds")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.SYNC_START,
                         f"Expected SYNC_START (11), got {msg_type}")
        
        # Now check if the second instance has synchronized with the first
        time.sleep(1)
        self.send_message(self.sock2, self.program2_address, self.program2_port,
                          MessageType.GET_TIME)
        
        # Get the TIME response from second instance
        response = self.receive_message(self.sock2)
        self.assertIsNotNone(response, "No response received for GET_TIME message")
        msg_type, content, _ = response
        self.assertEqual(msg_type, MessageType.TIME,
                         f"Expected TIME (32), got {msg_type}")
        
        # Check sync level (should be 2 since it's synchronized with the first instance)
        sync_level = content[0]
        self.assertEqual(sync_level, 2, "The second node should be synchronized with the first instance")
        
        # Check the timestamp is close to the current time
        timestamp = struct.unpack('!Q', content[1:9])[0]
        current_time = int(time.time() * 1000)
        self.assertAlmostEqual(timestamp, current_time, delta=100,
                              msg="Timestamp in TIME message is not close to current time")

    def test_sync_status_timeout(self):
        """Test that the node times out if it doesn't receive a SYNC_START in time"""
        with capture_stderr(self.program1) as stderr_capture:
            # Connect to the node
            self.send_message(self.sock1, self.program1_address, self.program1_port,
                            MessageType.CONNECT)
            # Discard the ACK_CONNECT
            _ = self.receive_message(self.sock1)

            # Send a sync start to the node
            sync_content = self.create_sync_start_message(0, 0)
            self.send_message(self.sock1, self.program1_address, self.program1_port,
                              MessageType.SYNC_START, sync_content)

            # Receive the DELAY_REQUEST
            delay_rq = self.receive_message(self.sock1)
            self.assertIsNotNone(delay_rq, "No response received for SYNC_START message")
            msg_type, content, _ = delay_rq
            self.assertEqual(msg_type, MessageType.DELAY_REQUEST,
                             f"Expected DELAY_REQUEST (12), got {msg_type}")
            
            # Send the DELAY_RESPONSE
            delay_response_content = self.create_sync_start_message(0, 0)
            self.send_message(self.sock1, self.program1_address, self.program1_port,
                              MessageType.DELAY_RESPONSE, delay_response_content)
            
            # Wait for a while without sending a SYNC_START to the node
            print("Going to sleep for 30 secs - be patient")
            time.sleep(30)

            # Send a GET_TIME to the node to check its status
            self.send_message(self.sock2, self.program1_address, self.program1_port,
                              MessageType.GET_TIME) 
            # Get the TIME response
            response = self.receive_message(self.sock2)
            self.assertIsNotNone(response, "No response received for GET_TIME message")
            msg_type, content, _ = response
            self.assertEqual(msg_type, MessageType.TIME,
                             f"Expected TIME (32), got {msg_type}")

            # We should see that the node no longer has sync level 1
            sync_level = content[0]
            self.assertGreater(sync_level, 1, "The node should lose its sync level after a timeout")

if __name__ == '__main__':
    unittest.main()