package com.predictive.monitoring.service;

import com.predictive.monitoring.dto.MachineStatusDto;
import com.predictive.monitoring.dto.SiteStatusDto;
import com.predictive.monitoring.dto.SummaryDto;
import com.predictive.monitoring.entity.Machine;
import com.predictive.monitoring.entity.Site;
import com.predictive.monitoring.repository.AnomalyEventRepository;
import com.predictive.monitoring.repository.MachineRepository;
import com.predictive.monitoring.repository.SiteRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class MonitoringService {

    private final SiteRepository siteRepo;
    private final MachineRepository machineRepo;
    private final AnomalyEventRepository anomalyRepo;

    /** 전체 지점 목록 + 상태 요약 */
    public List<SiteStatusDto> getAllSites() {
        return siteRepo.findAllWithMachines().stream()
                .map(this::toSiteStatus)
                .toList();
    }

    /** 특정 지점의 기계 목록 + 상태 */
    public List<MachineStatusDto> getMachinesBySite(String siteId) {
        return machineRepo.findBySiteIdOrderByMachineId(siteId).stream()
                .map(this::toMachineStatus)
                .toList();
    }

    /** 전체 요약 지표 */
    public SummaryDto getSummary() {
        return SummaryDto.builder()
                .totalSites(siteRepo.count())
                .totalMachines(machineRepo.count())
                .normalMachines(machineRepo.countByStatus("NORMAL"))
                .warningMachines(machineRepo.countByStatus("WARNING"))
                .criticalMachines(machineRepo.countByStatus("CRITICAL"))
                .activeAnomalies(anomalyRepo.countByIsResolved(false))
                .criticalAnomalies(anomalyRepo.countBySeverityAndIsResolved("CRITICAL", false))
                .build();
    }

    // ── 변환 헬퍼 ─────────────────────────────────────────────────────────────

    private SiteStatusDto toSiteStatus(Site site) {
        List<Machine> machines = site.getMachines() != null ? site.getMachines() : List.of();
        long normal   = machines.stream().filter(m -> "NORMAL".equals(m.getStatus())).count();
        long warning  = machines.stream().filter(m -> "WARNING".equals(m.getStatus())).count();
        long critical = machines.stream().filter(m -> "CRITICAL".equals(m.getStatus())).count();

        String overall = "NORMAL";
        if (critical > 0) overall = "CRITICAL";
        else if (warning > 0) overall = "WARNING";

        return SiteStatusDto.builder()
                .siteId(site.getSiteId())
                .siteName(site.getSiteName())
                .location(site.getLocation())
                .totalMachines(machines.size())
                .normalCount(normal)
                .warningCount(warning)
                .criticalCount(critical)
                .overallStatus(overall)
                .machines(machines.stream().map(this::toMachineStatus).toList())
                .build();
    }

    private MachineStatusDto toMachineStatus(Machine m) {
        return MachineStatusDto.builder()
                .machineId(m.getMachineId())
                .machineName(m.getMachineName())
                .siteId(m.getSiteId())
                .status(m.getStatus())
                .updatedAt(m.getUpdatedAt())
                .build();
    }
}
