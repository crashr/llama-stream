# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Make port 8066 available to the world outside this container
# This should ideally match the proxy_port in your config.yaml
# Or you can pass it as an environment variable and read it in python if you want it dynamic at container run time
# For simplicity, we assume it will be 8066 or whatever is in config.yaml.
# The actual port exposed by the proxy is determined by config.yaml.
# This EXPOSE is more for documentation and for `docker run -P`.
# EXPOSE 8066 # (This is just documentation; the app reads port from config.yaml)

# Define the command to run your application
# It will look for config.yaml in the /app directory by default
CMD ["python", "llama-stream.py"]
