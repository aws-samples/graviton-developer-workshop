package com.aws.graviton.model;

import jakarta.persistence.*;

@Entity
public class DataRecord {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String content;
    
    @Lob
    private byte[] compressedData;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    
    public byte[] getCompressedData() { return compressedData; }
    public void setCompressedData(byte[] data) { this.compressedData = data; }
}
