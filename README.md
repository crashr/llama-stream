# Llama.cpp Server Tool Streaming Workaround

This project provides a Python-based reverse proxy designed to sit in front of a `llama.cpp` `llama-server` instance. Its primary goal is to **enable OpenAI-compatible streaming for responses that include tool calls (function calling)**, a feature currently not fully supported by `llama-server` in a streaming fashion when tools are involved.

When `llama-server` processes a request that results in tool calls, it typically sends the entire JSON response at once, even if `stream: true` was requested by the client. This proxy intercepts such responses, and if tool calls are present (or for any non-streaming JSON response to a chat completion endpoint), it simulates an OpenAI-style `text/event-stream` response. This makes it compatible with clients and frameworks that expect this specific streaming behavior for tool integration.

**This is intended as a temporary glue/shim server until `llama-server` natively supports robust streaming for tool call responses.**

## Problem Solved

`llama-server` (from `llama.cpp`) is a fantastic tool for serving local LLMs with an OpenAI-compatible API. However, a current limitation is its handling of streaming responses when `tool_calls` (function calls) are part of the model's output. Instead of streaming the tool call information progressively as OpenAI's API does, `llama-server` often sends the full, non-streamed JSON response.

This proxy addresses this by:
1.  Forcing `stream: false` in requests to the backend `llama-server` (to ensure it gets the complete response with potential tool calls).
2.  Receiving the full JSON response from `llama-server`.
3.  If the response is for a chat completion and contains tool calls (or is just a standard JSON response intended for streaming), it then synthesizes a `text/event-stream` in the OpenAI format, chunking the content and tool calls appropriately.
4.  Forwarding other requests (like `/v1/models`) as-is.

## Who Needs This?

This workaround is beneficial for applications and frameworks that:
*   Utilize the OpenAI SDK (or compatible libraries) to interact with LLMs.
*   Rely on **streaming responses** for a better user experience or real-time processing.
*   Employ **tool calling / function calling** features with models served by `llama-server`.

Specifically, if your application expects to receive `delta` updates containing `tool_calls` within a stream, and `llama-server` is not providing them in that manner, this proxy can bridge the gap.

**Examples of tools/frameworks that can benefit:**

*   **`Open Interpreter`**: When configured to use a local `llama-server` instance that supports tool calling, Open Interpreter expects streaming responses, including for tool execution steps. This proxy helps ensure compatibility.
*   **`n8n.io`**: When using n8n's LLM nodes (e.g., OpenAI node) with a custom `llama-server` backend for models with function/tool calling capabilities, n8n might expect OpenAI-compliant streaming for these interactions.

## Features

*   **Simulated Streaming for Tool Calls:** Converts `llama-server`'s non-streamed tool call responses into an OpenAI-compatible `text/event-stream`.
*   **Configurable:** All key parameters (ports, target server URL, SSL verification) are managed via a `config.yaml` file.
*   **Flexible Target Server Connection:**
    *   Connects to HTTP backends.
    *   Connects to HTTPS backends with standard SSL certificate verification.
    *   Connects to HTTPS backends ignoring SSL certificate errors (useful for development with self-signed certificates).
*   **Dockerized:** Includes `Dockerfile` and `docker-compose.yml` for easy deployment.
*   **Basic Logging:** Provides insight into requests and responses.

## Prerequisites

*   Python 3.8+
*   pip (Python package installer)
*   A running instance of `llama-server` (from `llama.cpp`) that is configured with a model capable of tool/function calling.
*   Docker and Docker Compose (Recommended for easy deployment).

## Configuration

Configuration is handled via a `config.yaml` file. An example `config.yaml.example` is provided:

```yaml
# config.yaml.example

# Port on which the reverse proxy server will listen
proxy_port: 8066

# Target server configuration (your llama-server instance)
# Examples:
#   For HTTP: "http://localhost:8080"
#   For HTTPS with certificate verification: "https://secure.llama.server.com"
#   For HTTPS ignoring certificate errors: "https://localhost:8081" # if llama-server uses HTTPS with self-signed cert
target_url: "http://localhost:8080" # Adjust to your llama-server address

# SSL verification for the target_url (only if target_url is HTTPS)
# true: Verify SSL certificate (default)
# false: Do not verify SSL certificate
# string: Path to a CA bundle file or directory
verify_ssl: true

# Chunk size for simulated text streaming from a complete response
streaming_chunk_size: 50

# Optional: Request timeout for backend requests (seconds)
# request_timeout: 30

# Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
log_level: "INFO"
```
