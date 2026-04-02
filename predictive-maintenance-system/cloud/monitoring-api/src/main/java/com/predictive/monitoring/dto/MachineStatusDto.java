package com.predictive.monitoring.dto;

import lombok.Builder;
import lombok.Data;
import java.time.Instant;

@Data @Builder
public class MachineStatusDto {
    private String machineId;
    private String machineName;
    private String siteId;
    private String status;
    private Instant updatedAt;
}
