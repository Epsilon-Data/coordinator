"""
Main enclave server that listens on vsock and handles client connections
"""
import json
import socket
import logging
from typing import Optional

from config import VSOCK_PORT, MAX_REQUEST_SIZE, LOG_FORMAT, LOG_LEVEL
from request_handler import RequestHandler

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)


class EnclaveServer:
    """VSock server for handling enclave requests"""
    
    def __init__(self):
        self.socket: Optional[socket.socket] = None
        self.request_handler = RequestHandler()
        
    def start(self):
        """Start the vsock server and listen for connections"""
        try:
            # Create vsock socket
            self.socket = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            
            # CID_ANY allows connections from any CID
            self.socket.bind((socket.VMADDR_CID_ANY, VSOCK_PORT))
            self.socket.listen(5)
            
            logger.info(f"🚀 Enclave server started")
            logger.info(f"📡 Listening on vsock port {VSOCK_PORT}")
            
            # Main server loop
            while True:
                try:
                    # Accept connections
                    client_socket, client_addr = self.socket.accept()
                    logger.info(f"📥 Connection from CID: {client_addr}")
                    
                    # Handle client in same thread (for simplicity)
                    self.handle_client(client_socket)
                    
                except Exception as e:
                    logger.error(f"Error accepting connection: {str(e)}")
                    continue
                    
        except KeyboardInterrupt:
            logger.info("⏹️  Server shutdown requested")
        except Exception as e:
            logger.error(f"❌ Server error: {str(e)}")
        finally:
            self.cleanup()
            
    def handle_client(self, client_socket: socket.socket):
        """Handle a single client connection"""
        try:
            # Receive request data
            data = client_socket.recv(MAX_REQUEST_SIZE).decode('utf-8')
            if not data:
                logger.warning("Empty request received")
                return
                
            logger.info(f"📨 Received request: {len(data)} bytes")
            
            # Process request
            response = self.request_handler.handle_request(data)
            
            # Send response
            response_json = json.dumps(response)
            client_socket.send(response_json.encode())
            
            status_emoji = "✅" if response.get('status') == 'success' else "❌"
            logger.info(f"{status_emoji} Sent response: {response.get('status')}")
            
        except socket.timeout:
            logger.error("Client connection timed out")
            self._send_error(client_socket, "Request timeout")
        except Exception as e:
            logger.error(f"Error handling client: {str(e)}")
            self._send_error(client_socket, f"Server error: {str(e)}")
        finally:
            client_socket.close()
            
    def _send_error(self, client_socket: socket.socket, error_msg: str):
        """Send error response to client"""
        try:
            error_response = {
                "status": "error",
                "message": error_msg
            }
            client_socket.send(json.dumps(error_response).encode())
        except:
            # Best effort - ignore errors when sending error response
            pass
            
    def cleanup(self):
        """Clean up server resources"""
        if self.socket:
            try:
                self.socket.close()
                logger.info("🧹 Server socket closed")
            except:
                pass