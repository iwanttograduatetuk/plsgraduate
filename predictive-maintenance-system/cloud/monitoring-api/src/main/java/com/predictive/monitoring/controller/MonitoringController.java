package com.predictive.monitoring.controller;

import com.predictive.monitoring.dto.MachineStatusDto;
import com.predictive.monitoring.dto.SiteStatusDto;
import com.predictive.monitoring.dto.SummaryDto;
import com.predictive.monitoring.entity.AnomalyEvent;
import com.predictive.monitoring.entity.FaultDiagnosisResult;
import com.predictive.monitoring.repository.AnomalyEventRepository;
import com.predictive.monitoring.repository.FaultDiagnosisResultRepository;
import com.predictive.monitoring.service.InfluxQueryService;
import com.predictive.monitoring.service.MonitoringService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Monitoring REST API
 * ──────────────────────────────────────────────────────────────
 * Grafana JSON Datasource 및 중앙관리 대시보드에서 호출.
 *
 * 엔드포인트:
 *   GET /api/sites                              전체 지점 목록 + 상태
 *   GET /api/sites/{siteId}/machines            지점 내 기계 목록
 *   GET /api/machines/{machineId}/telemetry     시계열 텔레메트리
 *   GET /api/anomalies/recent                   최근 이상 이벤트
 *   GET /api/anomalies/{eventId}/diagnosis      원인 분석 결과
 *   GET /api/metrics/summary                    전체 지점 요약 지표
 */
@Slf4j
@RestController
@RequestMapping("/api")
@RequiredArgsConstructor
public class MonitoringController {

    private final MonitoringService monitoringService;
    private final InfluxQueryService influxQueryService;
    private final AnomalyEventRepository anomalyRepo;
    private final FaultDiagnosisResultRepository diagnosisRepo;

    // ── 지점 ──────────────────────────────────────────────────────────────────

    /**
     * 전체 지점 목록 + 각 지점의 기계 상태 요약
     * GET /api/sites
     */
    @GetMapping("/sites")
    public ResponseEntity<List<SiteStatusDto>> getSites() {
        return ResponseEntity.ok(monitoringService.getAllSites());
    }

    /**
     * 특정 지점의 기계 목록 + 상태
     * GET /api/sites/{siteId}/machines
     */
    @GetMapping("/sites/{siteId}/machines")
    public ResponseEntity<List<MachineStatusDto>> getMachinesBySite(
            @PathVariable String siteId
    ) {
        return ResponseEntity.ok(monitoringService.getMachinesBySite(siteId));
    }

    // ── 텔레메트리 ────────────────────────────────────────────────────────────

    /**
     * 기계별 이상 점수 시계열 (InfluxDB)
     * GET /api/machines/{machineId}/telemetry?siteId=site-A&range=60
     */
    @GetMapping("/machines/{machineId}/telemetry")
    public ResponseEntity<List<Map<String, Object>>> getTelemetry(
            @PathVariable String machineId,
            @RequestParam String siteId,
            @RequestParam(defaultValue = "60") int range   // 분 단위
    ) {
        List<Map<String, Object>> data = influxQueryService.getTelemetrySeries(siteId, machineId, range);
        return ResponseEntity.ok(data);
    }

    /**
     * 전체 최신 이상 점수 (Grafana 개요 패널용)
     * GET /api/machines/scores/latest
     */
    @GetMapping("/machines/scores/latest")
    public ResponseEntity<List<Map<String, Object>>> getLatestScores() {
        return ResponseEntity.ok(influxQueryService.getLatestScores());
    }

    // ── 이상 이벤트 ───────────────────────────────────────────────────────────

    /**
     * 최근 이상 이벤트 (페이징)
     * GET /api/anomalies/recent?siteId=site-A&page=0&size=20
     */
    @GetMapping("/anomalies/recent")
    public ResponseEntity<Page<AnomalyEvent>> getRecentAnomalies(
            @RequestParam(required = false) String siteId,
            @RequestParam(defaultValue = "0")  int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        PageRequest pageable = PageRequest.of(page, size, Sort.by("detectedAt").descending());
        Page<AnomalyEvent> result = (siteId != null && !siteId.isBlank())
                ? anomalyRepo.findBySiteIdOrderByDetectedAtDesc(siteId, pageable)
                : anomalyRepo.findAllByOrderByDetectedAtDesc(pageable);
        return ResponseEntity.ok(result);
    }

    /**
     * 특정 기계의 이상 이벤트
     * GET /api/anomalies/machine/{machineId}
     */
    @GetMapping("/anomalies/machine/{machineId}")
    public ResponseEntity<Page<AnomalyEvent>> getAnomaliesByMachine(
            @PathVariable String machineId,
            @RequestParam(defaultValue = "0")  int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        PageRequest pageable = PageRequest.of(page, size, Sort.by("detectedAt").descending());
        return ResponseEntity.ok(anomalyRepo.findByMachineIdOrderByDetectedAtDesc(machineId, pageable));
    }

    /**
     * 특정 이상 이벤트의 Fault Diagnosis 결과
     * GET /api/anomalies/{eventId}/diagnosis
     */
    @GetMapping("/anomalies/{eventId}/diagnosis")
    public ResponseEntity<?> getDiagnosis(@PathVariable UUID eventId) {
        return diagnosisRepo.findByEventId(eventId)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    // ── 요약 지표 ─────────────────────────────────────────────────────────────

    /**
     * 전체 지점 요약 지표 (대시보드 헤더용)
     * GET /api/metrics/summary
     */
    @GetMapping("/metrics/summary")
    public ResponseEntity<SummaryDto> getSummary() {
        return ResponseEntity.ok(monitoringService.getSummary());
    }

    // ── 헬스체크 ─────────────────────────────────────────────────────────────

    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> health() {
        return ResponseEntity.ok(Map.of("status", "ok", "service", "monitoring-api"));
    }
}
