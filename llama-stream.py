import http.server
import requests
import json
import time
import yaml
import logging
from functools import partial

# Global config dictionary
CONFIG = {}

def load_config(config_path="config.yaml"):
    """Loads configuration from a YAML file."""
    global CONFIG
    try:
        with open(config_path, 'r') as f:
            CONFIG = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file {config_path} not found. Exiting.")
        exit(1)
    except yaml.YAMLError as e:
        logging.error(f"Error parsing configuration file {config_path}: {e}. Exiting.")
        exit(1)
    
    # Setup logging based on config
    log_level_str = CONFIG.get("log_level", "INFO").upper()
    numeric_level = getattr(logging, log_level_str, None)
    if not isinstance(numeric_level, int):
        logging.warning(f"Invalid log level: {log_level_str}. Defaulting to INFO.")
        numeric_level = logging.INFO
    logging.basicConfig(level=numeric_level, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Configuration loaded successfully.")


class ReverseProxy(http.server.BaseHTTPRequestHandler):
    # CONFIG will be injected by the partial function in run()
    # We can access it via self.config if needed for instance-specific config
    # or directly via the global CONFIG if it's set globally before server start.
    # For cleaner dependency injection, we'll pass it to the constructor.

    def __init__(self, *args, config, **kwargs):
        self.config = config # Store config for this instance
        super().__init__(*args, **kwargs)

    def _get_target_url(self):
        return self.config.get("target_url", "http://localhost:8000") # Default if not in config

    def _get_verify_ssl(self):
        # Handles boolean true/false or path to CA bundle string
        verify = self.config.get("verify_ssl", True)
        if isinstance(verify, str) and not verify.lower() in ["true", "false"]:
            # If it's a string and not 'true'/'false', assume it's a path to CA bundle
            return verify 
        return str(verify).lower() == 'true' # Convert "true"/"false" strings to bool

    def _get_request_timeout(self):
        return self.config.get("request_timeout", None) # None means requests default

    def _perform_request(self, method, path, headers, data=None, json_data=None):
        target_url = self._get_target_url()
        verify_ssl = self._get_verify_ssl() if target_url.startswith("https://") else True
        timeout = self._get_request_timeout()
        
        # Only forward essential headers, or define a whitelist/blacklist
        forward_headers = {
            "Authorization": headers.get('Authorization', ''),
            "Content-Type": headers.get('Content-Type', 'application/json'), # Default content type
            "Accept": headers.get('Accept', '*/*')
        }
        # Filter out empty headers
        forward_headers = {k: v for k, v in forward_headers.items() if v}

        try:
            logging.debug(f"Forwarding {method} request to: {target_url}{path}")
            logging.debug(f"Headers: {forward_headers}")
            if json_data: logging.debug(f"JSON_Data: {json_data}")
            elif data: logging.debug(f"Data: {data[:200]}...") # Log only first 200 bytes of raw data

            response = requests.request(
                method,
                f"{target_url}{path}",
                headers=forward_headers,
                data=data,
                json=json_data,
                verify=verify_ssl,
                timeout=timeout,
                stream=True # Important for handling large responses / streaming
            )
            logging.debug(f"Received {response.status_code} from target server.")
            return response
        except requests.exceptions.SSLError as e:
            logging.error(f"SSL Error connecting to {target_url}: {e}")
            self.send_error(502, f"SSL Error: {e}")
            return None
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection Error connecting to {target_url}: {e}")
            self.send_error(503, f"Service Unavailable: Connection Error: {e}")
            return None
        except requests.exceptions.Timeout:
            logging.error(f"Request to {target_url} timed out.")
            self.send_error(504, "Gateway Timeout")
            return None
        except Exception as e:
            logging.error(f"Generic error during request to {target_url}: {e}")
            self.send_error(500, f"Internal Server Error: {e}")
            return None

    def do_GET(self):
        # Basic path check, can be expanded with self.config.get("allowed_get_paths", [])
        if self.path == "/v1/models": # Or check against a configurable list of paths
            response = self._perform_request("GET", self.path, self.headers)
            if response:
                self.send_response(response.status_code)
                # Forward relevant headers from the backend response
                for key, value in response.headers.items():
                    if key.lower() in ["content-type", "content-length", "date"]: # Add more if needed
                        self.send_header(key, value)
                self.end_headers()
                # Stream content
                for chunk in response.iter_content(chunk_size=8192): # 8KB chunks
                    self.wfile.write(chunk)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data_bytes = self.rfile.read(content_length)
        
        try:
            request_data = json.loads(post_data_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            logging.warning("POST data is not valid JSON. Proxying as raw bytes.")
            # If data is not JSON, we might want to proxy it as-is without modification
            # For now, the original logic assumes JSON and modifies 'stream'
            # Let's try to stick to that, but provide a fallback or error
            self.send_error(400, "Bad Request: Invalid JSON payload")
            return

        # Original logic: force stream to false for the backend
        request_data['stream'] = False 

        response = self._perform_request("POST", self.path, self.headers, json_data=request_data)

        if response:
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type',''):
                # If backend responds with 200 and JSON, simulate streaming if it's not already
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close") # Keep-alive can be problematic with naive streaming
                self.end_headers()

                try:
                    response_json_data = response.json()
                except json.JSONDecodeError:
                    logging.error("Backend returned 200 but non-JSON content, cannot simulate stream.")
                    # Fallback to proxying the raw content if it wasn't JSON
                    self.wfile.write(response.content) # response.content would have been read by response.json()
                    return # Exit early as we can't stream this

                for chunk in self._simulate_streaming(response_json_data):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode('utf-8'))
                    self.wfile.flush()
                self.wfile.write("data: [DONE]\n\n".encode('utf-8'))
                self.wfile.flush()
            else:
                # Proxy non-200 or non-JSON responses as they are
                self.send_response(response.status_code)
                for key, value in response.headers.items():
                    if key.lower() in ["content-type", "content-length", "date"]: # Add more if needed
                        self.send_header(key, value)
                self.end_headers()
                for chunk in response.iter_content(chunk_size=8192):
                    self.wfile.write(chunk)
                self.wfile.flush()

    def _simulate_streaming(self, response_data):
        """
        Simulates OpenAI-like streaming response from a complete JSON response.
        """
        streaming_chunk_size = self.config.get("streaming_chunk_size", 50)
        
        if "choices" in response_data and isinstance(response_data["choices"], list) and response_data["choices"]:
            choice = response_data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", None)

            base_event_data = {
                "id": response_data.get("id", "chatcmpl-default-id"),
                "object": response_data.get("object", "chat.completion.chunk"), # Simulate chunk object
                "created": response_data.get("created", int(time.time())),
                "model": response_data.get("model", "gpt-3.5-turbo-0613"), # Default model
                # system_fingerprint is also common in OpenAI responses
            }

            if tool_calls:
                # Send tool_calls in a single chunk, mimicking OpenAI
                yield {
                    **base_event_data,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            elif content:
                # Simulate text streaming
                for i in range(0, len(content), streaming_chunk_size):
                    text_chunk = content[i:i+streaming_chunk_size]
                    yield {
                        **base_event_data,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": text_chunk} if i == 0 else {"content": text_chunk},
                                "finish_reason": None,
                            }
                        ],
                    }
                # Send the final chunk with finish_reason
                yield {
                    **base_event_data,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],  
                }
            else: # Empty content but no tool_calls
                yield {
                    **base_event_data,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop", # Or "length" if applicable
                        }
                    ],
                }
        else: # Non-OpenAI-like structure, or error structure from backend
            logging.warning(f"Response data does not have 'choices' or it's empty. Yielding as is: {response_data}")
            # Yield the original data wrapped in a minimal streaming structure if it's not suitable
            yield {
                "id": response_data.get("id", "chatcmpl-default-id"),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "unknown-model",
                "choices": [{"index": 0, "delta": {"content": json.dumps(response_data)}, "finish_reason": None}],
            }
            yield {
                "id": response_data.get("id", "chatcmpl-default-id"),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "unknown-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }

    def log_message(self, format, *args):
        """Override to use Python's logging module."""
        logging.info(format % args)

    def log_error(self, format, *args):
        """Override to use Python's logging module."""
        logging.error(format % args)


def run(config_file="config.yaml"):
    load_config(config_file) # Load config globally for now
    
    proxy_port = CONFIG.get("proxy_port", 8066)
    server_address = ('', proxy_port)

    # Use functools.partial to pass the config to the handler's constructor
    # This is a cleaner way than relying on globals within the class instances
    HandlerWithConfig = partial(ReverseProxy, config=CONFIG)
    
    httpd = http.server.HTTPServer(server_address, HandlerWithConfig)
    logging.info(f"Starting reverse proxy on port {proxy_port}...")
    logging.info(f"Targeting backend: {CONFIG.get('target_url')}")
    if CONFIG.get('target_url', '').startswith("https://"):
        logging.info(f"SSL Verification for target: {CONFIG.get('verify_ssl', True)}")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Server shutting down...")
    finally:
        httpd.server_close()
        logging.info("Server closed.")

if __name__ == "__main__":
    # You could make config_file an argument:
    import sys
    conf_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run(conf_file)
