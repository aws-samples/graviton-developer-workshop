package com.aws.graviton.service;

import net.jpountz.lz4.LZ4Compressor;
import net.jpountz.lz4.LZ4Factory;
import net.jpountz.lz4.LZ4FastDecompressor;
import org.springframework.stereotype.Service;
import org.xerial.snappy.Snappy;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

@Service
public class CompressionService {
    private final LZ4Factory lz4Factory = LZ4Factory.fastestInstance();

    public byte[] compressSnappy(String data) throws IOException {
        return Snappy.compress(data.getBytes(StandardCharsets.UTF_8));
    }

    public String decompressSnappy(byte[] compressed) throws IOException {
        return new String(Snappy.uncompress(compressed), StandardCharsets.UTF_8);
    }

    public byte[] compressLZ4(String data) {
        byte[] src = data.getBytes(StandardCharsets.UTF_8);
        LZ4Compressor compressor = lz4Factory.fastCompressor();
        int maxLen = compressor.maxCompressedLength(src.length);
        byte[] compressed = new byte[maxLen];
        int compressedLen = compressor.compress(src, 0, src.length, compressed, 0, maxLen);
        byte[] result = new byte[compressedLen];
        System.arraycopy(compressed, 0, result, 0, compressedLen);
        return result;
    }

    public String decompressLZ4(byte[] compressed, int originalLength) {
        LZ4FastDecompressor decompressor = lz4Factory.fastDecompressor();
        byte[] restored = new byte[originalLength];
        decompressor.decompress(compressed, 0, restored, 0, originalLength);
        return new String(restored, StandardCharsets.UTF_8);
    }
}
