#!/bin/bash
set -e

echo "Building project..."
mvn clean package -DskipTests

echo "Starting application..."
java -jar target/migration-test-1.0.0.jar &
APP_PID=$!

sleep 10

echo "Running tests..."
echo "1. Health check:"
curl -s http://localhost:8080/api/health | jq .

echo -e "\n2. Snappy compression test:"
curl -s -X POST http://localhost:8080/api/compress/snappy \
  -H "Content-Type: application/json" \
  -d '{"data":"'$(head -c 1000 /dev/urandom | base64)'"}' | jq .

echo -e "\n3. LZ4 compression test:"
curl -s -X POST http://localhost:8080/api/compress/lz4 \
  -H "Content-Type: application/json" \
  -d '{"data":"'$(head -c 1000 /dev/urandom | base64)'"}' | jq .

echo -e "\n4. Running benchmark:"
curl -s http://localhost:8080/api/benchmark | jq .

kill $APP_PID
echo -e "\nTests completed!"
