package com.predictive.monitoring.service;

import com.predictive.monitoring.dto.CommandRequest;
import com.predictive.monitoring.entity.AnomalyEvent;
import com.predictive.monitoring.entity.Machine;
import com.predictive.monitoring.repository.AnomalyEventRepository;
import com.predictive.monitoring.repository.MachineRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.MediaType;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class MachineCommandService {

    private final MachineRepository machineRepo;
    private final AnomalyEventRepository anomalyRepo;
    private final RestClient restClient = RestClient.create();

    @Value("${edge.base-url}")
    private String edgeBaseUrl;

    /**
     * 기계 제어 명령 처리
     * 1. PostgreSQL Machine 상태 업데이트
     * 2. Edge agent HTTP 호출 (STOP/RESUME)
     * 3. ACK면 이상 이벤트 resolved 처리
     */
    @Transactional
    public Map<String, Object> execute(String machineId, CommandRequest req) {
        String cmd = req.getCommand();

        // 1. Machine 상태 업데이트
        Machine machine = machineRepo.findById(machineId)
                .orElseThrow(() -> new IllegalArgumentException("기계 없음: " + machineId));

        String prevStatus = machine.getStatus();

        switch (cmd) {
            case "STOP"   -> machine.setStatus("CRITICAL");
            case "RESUME" -> machine.setStatus("NORMAL");
            // ACK는 상태 변경 없이 이벤트만 처리
        }
        machine.setUpdatedAt(Instant.now());
        machineRepo.save(machine);

        // 2. ACK: 이상 이벤트 resolved 처리
        if ("ACK".equals(cmd) && req.getEventId() != null) {
            try {
                UUID eventId = UUID.fromString(req.getEventId());
                anomalyRepo.findById(eventId).ifPresent(event -> {
                    event.setIsResolved(true);
                    anomalyRepo.save(event);
                });
            } catch (IllegalArgumentException e) {
                log.warn("잘못된 eventId 형식: {}", req.getEventId());
            }
        }

        // 3. Edge HTTP 호출 (STOP / RESUME 만)
        String edgeResult = "skipped";
        if ("STOP".equals(cmd) || "RESUME".equals(cmd)) {
            edgeResult = callEdge(machineId, cmd, req.getReason());
        }

        log.info("명령 처리 완료: machine={} cmd={} prevStatus={} → {}",
                machineId, cmd, prevStatus, machine.getStatus());

        return Map.of(
                "machineId",  machineId,
                "command",    cmd,
                "status",     machine.getStatus(),
                "edgeResult", edgeResult,
                "timestamp",  Instant.now().toString()
        );
    }

    private String callEdge(String machineId, String command, String reason) {
        try {
            Map<String, String> body = Map.of(
                    "machine_id", machineId,
                    "command",    command,
                    "reason",     reason != null ? reason : ""
            );
            String json = new ObjectMapper().writeValueAsString(body);
            restClient.post()
                    .uri(edgeBaseUrl + "/command")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(json)
                    .retrieve()
                    .toBodilessEntity();
            log.info("Edge 명령 전송 성공: {} → {}", machineId, command);
            return "ok";
        } catch (Exception e) {
            log.warn("Edge 명령 전송 실패 (오프라인?): {} — {}", machineId, e.getMessage());
            return "edge_offline";
        }
    }
}
