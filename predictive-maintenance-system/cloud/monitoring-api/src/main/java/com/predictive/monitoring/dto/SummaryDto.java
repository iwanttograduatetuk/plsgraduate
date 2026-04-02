package com.predictive.monitoring.dto;

import lombok.Builder;
import lombok.Data;

@Data @Builder
public class SummaryDto {
    private long totalSites;
    private long totalMachines;
    private long normalMachines;
    private long warningMachines;
    private long criticalMachines;
    private long activeAnomalies;        // is_resolved = false
    private long criticalAnomalies;     // severity = CRITICAL, is_resolved = false
}
