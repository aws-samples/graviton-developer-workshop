FROM python:3.12-slim

WORKDIR /app

# Install data science libraries for trading calculations
RUN pip install --no-cache-dir \
    pandas==2.2.2 \
    numpy==2.0.1 \
    scipy==1.14.0 \
    matplotlib==3.9.1

# Copy the sandbox HTTP server
COPY sandbox_server.py /app/sandbox_server.py

# Create working directory for user code and data
RUN mkdir -p /app/output

EXPOSE 8888

# Run the sandbox HTTP server
CMD ["python3", "/app/sandbox_server.py"]
