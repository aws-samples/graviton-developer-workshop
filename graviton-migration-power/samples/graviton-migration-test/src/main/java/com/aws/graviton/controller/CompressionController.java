package com.aws.graviton.controller;

import com.aws.graviton.service.CompressionService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class CompressionController {

    @Autowired
    private CompressionService compressionService;

    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> health() {
        Map<String, String> response = new HashMap<>();
        response.put("status", "UP");
        response.put("architecture", System.getProperty("os.arch"));
        return ResponseEntity.ok(response);
    }

    @PostMapping("/compress/snappy")
    public ResponseEntity<Map<String, Object>> compressSnappy(@RequestBody Map<String, String> request) {
        try {
            String data = request.get("data");
            long start = System.nanoTime();
            byte[] compressed = compressionService.compressSnappy(data);
            long duration = System.nanoTime() - start;

            Map<String, Object> response = new HashMap<>();
            response.put("originalSize", data.length());
            response.put("compressedSize", compressed.length);
            response.put("ratio", String.format("%.2f", (double) data.length() / compressed.length));
            response.put("durationMs", duration / 1_000_000.0);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }

    @PostMapping("/compress/lz4")
    public ResponseEntity<Map<String, Object>> compressLZ4(@RequestBody Map<String, String> request) {
        String data = request.get("data");
        long start = System.nanoTime();
        byte[] compressed = compressionService.compressLZ4(data);
        long duration = System.nanoTime() - start;

        Map<String, Object> response = new HashMap<>();
        response.put("originalSize", data.length());
        response.put("compressedSize", compressed.length);
        response.put("ratio", String.format("%.2f", (double) data.length() / compressed.length));
        response.put("durationMs", duration / 1_000_000.0);
        return ResponseEntity.ok(response);
    }

    @GetMapping("/benchmark")
    public ResponseEntity<Map<String, Object>> benchmark() {
        String testData = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. ".repeat(100);
        
        try {
            long snappyStart = System.nanoTime();
            for (int i = 0; i < 1000; i++) {
                compressionService.compressSnappy(testData);
            }
            long snappyDuration = System.nanoTime() - snappyStart;

            long lz4Start = System.nanoTime();
            for (int i = 0; i < 1000; i++) {
                compressionService.compressLZ4(testData);
            }
            long lz4Duration = System.nanoTime() - lz4Start;

            Map<String, Object> response = new HashMap<>();
            response.put("iterations", 1000);
            response.put("snappyMs", snappyDuration / 1_000_000.0);
            response.put("lz4Ms", lz4Duration / 1_000_000.0);
            response.put("architecture", System.getProperty("os.arch"));
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }
}
