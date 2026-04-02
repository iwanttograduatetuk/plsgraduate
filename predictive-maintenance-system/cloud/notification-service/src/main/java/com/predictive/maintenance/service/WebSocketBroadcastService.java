package com.predictive.maintenance.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * WebSocket STOMP 기반 실시간 알림 브로드캐스트.
 *
 * 토픽:
 *   /topic/anomalies         — 이상 이벤트 실시간 스트림 (중앙관리자)
 *   /topic/anomalies/{siteId} — 지점별 이벤트
 *   /topic/diagnosis          — Fault Diagnosis 결과
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class WebSocketBroadcastService {

    private final SimpMessagingTemplate messagingTemplate;
    private final ObjectMapper objectMapper;

    /** 이상 이벤트 전체 브로드캐스트 */
    public void broadcastAnomaly(Map<String, Object> event) {
        try {
            messagingTemplate.convertAndSend("/topic/anomalies", event);
            String siteId = (String) event.getOrDefault("site_id", "unknown");
            messagingTemplate.convertAndSend("/topic/anomalies/" + siteId, event);
            log.debug("WebSocket 브로드캐스트: anomaly site={}", siteId);
        } catch (Exception e) {
            log.error("WebSocket 브로드캐스트 실패: {}", e.getMessage());
        }
    }

    /** Fault Diagnosis 결과 브로드캐스트 */
    public void broadcastDiagnosis(Map<String, Object> result) {
        try {
            messagingTemplate.convertAndSend("/topic/diagnosis", result);
            String siteId = (String) result.getOrDefault("site_id", "unknown");
            messagingTemplate.convertAndSend("/topic/diagnosis/" + siteId, result);
            log.debug("WebSocket 브로드캐스트: diagnosis site={}", siteId);
        } catch (Exception e) {
            log.error("WebSocket 진단결과 브로드캐스트 실패: {}", e.getMessage());
        }
    }
}
