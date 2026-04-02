package com.predictive.monitoring.dto;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data @Builder
public class SiteStatusDto {
    private String siteId;
    private String siteName;
    private String location;
    private long totalMachines;
    private long normalCount;
    private long warningCount;
    private long criticalCount;
    /** 지점 전체 상태: NORMAL / WARNING / CRITICAL */
    private String overallStatus;
    private List<MachineStatusDto> machines;
}
