#!/usr/bin/env python3
import os
import io
import sys
import tempfile
import time
import contextlib
import socket
import struct
import select
import threading
from typing import Optional, Tuple, List, Any, Union, Callable
from enum import IntEnum
from contextlib import contextmanager


class MessageType(IntEnum):
    """Message types matching the C++ enum"""
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


class LeaderState(IntEnum):
    """Leader states matching the C++ enum"""
    LEADER_BEGIN = 0
    LEADER_STOP = 255


# Global flag to indicate when stderr capture is active
_stderr_capture_active = threading.Event()

def start_stderr_capture():
    """Set the global flag indicating stderr capture is active"""
    _stderr_capture_active.set()
    
def end_stderr_capture():
    """Clear the global flag indicating stderr capture is active"""
    _stderr_capture_active.clear()
    
def is_stderr_capture_active():
    """Check if stderr capture is active"""
    return _stderr_capture_active.is_set()


@contextmanager
def capture_stderr(process):
    """
    Context manager to capture stderr from a process that was started with stderr=subprocess.PIPE.
    
    Usage:
    ```
    with capture_stderr(self.program1) as stderr_capture:
        # do something that should generate stderr output
        send_invalid_message(...)
        
    # stderr_capture now contains the captured output
    self.assertIn("ERROR", stderr_capture.get_output())
    ```
    """
    if process.stderr is None:
        raise ValueError("Process stderr must be redirected to a pipe (stderr=subprocess.PIPE)")
        
    # Let the reader thread know we're capturing stderr now
    start_stderr_capture()

    class StderrCapture:
        def __init__(self):
            self.output = ""
            
        def get_output(self, timeout=2.0):
            """
            Get captured output with a timeout.
            
            Args:
                timeout: Maximum time in seconds to wait for output.
                         Set to None for no timeout (not recommended).
            
            Returns:
                str: The captured stderr output
            """
            start_time = time.time()
            
            # Check if there's data available to read with timeout
            while timeout is None or (time.time() - start_time) < timeout:
                readable, _, _ = select.select([process.stderr], [], [], 0.1)
                
                if process.stderr in readable:
                    # Read available data (non-blocking)
                    line = process.stderr.readline()
                    if line:
                        self.output += line
                    else:
                        # Small wait to prevent CPU spinning
                        time.sleep(0.05)
                else:
                    # If no data available after 0.5s and we have some output, we're probably done
                    if self.output and (time.time() - start_time) > 0.5:
                        break
                    # Small wait to prevent CPU spinning
                    time.sleep(0.05)
                        
            return self.output
            
        def clear_output(self, timeout=1.0):
            """
            Clear the captured output buffer with timeout
            
            Args:
                timeout: Maximum time in seconds to wait for output.
                         Set to None for no timeout (not recommended).
            
            Returns:
                str: The previous content of the buffer
            """
            # Read any new data first with timeout to ensure we don't lose it
            self.get_output(timeout)
            
            # Store what we've read so far
            old_output = self.output
            
            # Clear the buffer
            self.output = ""
            return old_output

    capture = StderrCapture()
    
    try:
        yield capture
    finally:
        # Signal that we're done capturing stderr
        end_stderr_capture()
        

def create_invalid_message(message_type=None, content=None) -> bytes:
    """
    Creates an invalid message for testing error handling.
    
    Args:
        message_type: Optional invalid message type. If None, uses 100 (undefined type)
        content: Optional content bytes. If None, uses b'INVALID_CONTENT'
        
    Returns:
        bytes: The invalid message as bytes
    """
    if message_type is None:
        message_type = 100  # Invalid message type
        
    if content is None:
        content = b'INVALID_CONTENT'
        
    message = bytes([message_type])
    if content is not None:
        message += content
    
    return message


def create_malformed_message(valid_type: MessageType, malformation_type: str = 'truncated') -> bytes:
    """
    Creates a malformed but recognizable message for testing error handling
    
    Args:
        valid_type: A valid MessageType to base the malformed message on
        malformation_type: Type of malformation: 'truncated', 'wrong_size', 'bad_format'
        
    Returns:
        bytes: The malformed message
    """
    message = bytes([valid_type])
    
    if malformation_type == 'truncated':
        # For message types that expect content, send without content
        if valid_type in [MessageType.SYNC_START, MessageType.DELAY_REQUEST,
                          MessageType.DELAY_RESPONSE, MessageType.LEADER]:
            return message  # Just the message type, no content
            
    elif malformation_type == 'wrong_size':
        # Send wrong-sized content
        if valid_type in [MessageType.SYNC_START, MessageType.DELAY_REQUEST,
                          MessageType.DELAY_RESPONSE]:
            # These expect 9 bytes, send 5
            message += b'\x00\x00\x00\x00\x00'
        elif valid_type == MessageType.LEADER:
            # Expects 1 byte, send 2
            message += b'\x00\x00'
            
    elif malformation_type == 'bad_format':
        # Send content with correct size but bad format
        if valid_type in [MessageType.SYNC_START, MessageType.DELAY_REQUEST,
                          MessageType.DELAY_RESPONSE]:
            # These expect structured binary data, send text
            message += b'badformat123'  # 9 bytes but not properly structured
            
    return message


def send_and_assert_error(test_case, sock, address, port, 
                         message_type_or_bytes, content=None, 
                         process=None, error_text="ERROR", timeout=2.0):
    """
    Sends a message and asserts that the program logs an error.
    
    Args:
        test_case: The unittest.TestCase instance
        sock: The socket to send with
        address: Target address
        port: Target port
        message_type_or_bytes: Either a MessageType or raw bytes to send
        content: Optional content if message_type_or_bytes is a MessageType
        process: Process whose stderr to capture (required if asserting errors)
        error_text: Text to look for in the stderr output
        timeout: Maximum time in seconds to wait for error output
        
    Returns:
        str: The captured stderr output
    """
    if process is None:
        raise ValueError("Process must be specified to capture stderr")
        
    with capture_stderr(process) as stderr_capture:
        # Send the message
        if isinstance(message_type_or_bytes, bytes):
            sock.sendto(message_type_or_bytes, (address, port))
        else:
            message = bytes([message_type_or_bytes])
            if content is not None:
                message += content
            sock.sendto(message, (address, port))
        
        # Wait for processing
        time.sleep(0.5)
        
        # Get output with timeout
        output = stderr_capture.get_output(timeout=timeout)
        
        # Assert error presence
        test_case.assertIn(error_text, output.upper(),
                         f"Expected error text '{error_text}' not found in stderr")
        
        return output