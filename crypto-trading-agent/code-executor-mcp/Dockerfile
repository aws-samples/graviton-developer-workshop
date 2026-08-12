FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server.py .
COPY sandbox_runner.py .

# Expose MCP server port
EXPOSE 8000

# Run with uvicorn
CMD ["python", "server.py"]
